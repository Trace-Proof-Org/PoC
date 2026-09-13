"""Deterministic Bug Reproduction Agent.

Parses formal verification counterexamples from TLC and synthesizes a
deterministic, non-fungible runtime test harness to reproduce the bug directly
on the target Python codebase.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from agents.bug_reproducer.adapters import (
    AdapterError,
    AdapterRegistry,
    TargetAdapter,
    UnmappedActionError,
    parse_action_signature,
)
from agents.bug_reproducer.provenance import (
    MissingArtifactError,
    ProvenanceError,
    ProvenanceReceipt,
    compute_provenance,
)
from agents.bug_reproducer.templates import (
    build_dynamic_harness_script,
    build_harness_script,
)


class ReproducerError(Exception):
    """Base error for bug reproducer agent."""
    pass


class MissingCounterexampleError(ReproducerError):
    """Raised when the counterexample artifact is absent or has no counterexample."""
    pass


class MalformedCounterexampleError(ReproducerError):
    """Raised when the counterexample artifact is structurally invalid."""
    pass


@dataclass
class ReproductionResult:
    target: str
    reproduced: bool
    verdict: str
    final_counter: Optional[int]
    expected_counter: Optional[int]
    error_traceback: str
    test_script_path: str
    details: Dict[str, Any]
    violations_detected: int = 0
    provenance: Optional[Dict[str, str]] = None
    transition_mapping_table: Optional[List[Dict[str, Any]]] = None
    target_source_hash: Optional[str] = None
    spec_hash: Optional[str] = None
    cfg_hash: Optional[str] = None
    counterexample_hash: Optional[str] = None


def _simplify_tla_vars(vars_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Flattens tla-mcp variable representation into simplified python primitives."""
    simple = {}
    for k, v in vars_dict.items():
        if isinstance(v, dict):
            if "display" in v:
                disp = str(v["display"]).strip('"')
                simple[k] = disp
            elif "json" in v:
                simple[k] = v["json"]
            else:
                simple[k] = v
        else:
            simple[k] = v
    return simple


def _infer_action_from_transition(step: int, prev_state: Dict[str, Any], curr_state: Dict[str, Any]) -> str:
    """Infers the TLA+ action that caused the state transition when not explicitly provided."""
    prev_owner = prev_state.get("owner", prev_state.get("current_owner", "none"))
    curr_owner = curr_state.get("owner", curr_state.get("current_owner", "none"))
    prev_lease = str(prev_state.get("lease_valid", prev_state.get("lease_expiry", ""))).lower()
    curr_lease = str(curr_state.get("lease_valid", curr_state.get("lease_expiry", ""))).lower()

    # ExpireLease: lease went from valid/true/1 to invalid/false/0
    if prev_lease in ("true", "1") and curr_lease in ("false", "0"):
        return "ExpireLease"

    # Acquire: owner changed from none or another worker entered CS
    if curr_owner != "none" and curr_owner != prev_owner:
        return f'Acquire("{curr_owner}")'

    # Release
    if curr_owner == "none" and prev_owner != "none":
        return f'Release("{prev_owner}")'

    # Fallback: check pc changes
    pc_curr = str(curr_state.get("pc", ""))
    pc_prev = str(prev_state.get("pc", ""))
    if "InCS" in pc_curr and "InCS" not in pc_prev:
        m = re.search(r"([wW]\d+)\s*:>\s*\"InCS\"", pc_curr)
        if m:
            return f'Acquire("{m.group(1)}")'

    return f"Transition_{step}"


def _normalize_counterexample_states(raw_states: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Normalizes counterexample states into uniform dicts with:
    'step': int, 'action': str, 'state': Dict[str, Any].
    Supports both raw tla-mcp outputs and pre-annotated traces.
    """
    normalized: List[Dict[str, Any]] = []
    for i, item in enumerate(raw_states):
        step_num = item.get("step", i + 1)
        if "state" in item:
            state_dict = item["state"]
        elif "vars" in item:
            state_dict = _simplify_tla_vars(item["vars"])
        else:
            state_dict = {k: v for k, v in item.items() if k not in ("step", "action")}

        if "action" in item and item["action"]:
            action_name = item["action"]
        elif step_num == 1:
            action_name = "Init"
        else:
            prev_state = normalized[i - 1]["state"]
            action_name = _infer_action_from_transition(step_num, prev_state, state_dict)

        normalized.append({
            "step": step_num,
            "action": action_name,
            "state": state_dict,
        })
    return normalized


def parse_counterexample_from_manifest(manifest_path: Path) -> List[Dict[str, Any]]:
    """
    Extract and validate JSON counterexample trace from export/manifest.md.
    Fails closed if manifest is missing, lacks a counterexample, or contains malformed JSON.
    """
    if not manifest_path.exists():
        raise MissingCounterexampleError(f"Export manifest does not exist at: {manifest_path}")

    content = manifest_path.read_text(encoding="utf-8")
    if "COUNTEREXAMPLE FOUND" not in content:
        raise MissingCounterexampleError(
            f"No counterexample recorded in {manifest_path}. "
            f"Phase 5 model checking did not discover any invariant violations."
        )

    idx = content.find("### Counterexample")
    if idx == -1:
        raise MalformedCounterexampleError(
            f"Manifest indicates COUNTEREXAMPLE FOUND, but '### Counterexample' section is missing: {manifest_path}"
        )

    sub = content[idx:]
    match = re.search(r"(\[\s*\{.*\}\s*\])", sub, re.DOTALL)
    if not match:
        raise MalformedCounterexampleError(
            f"Failed to extract JSON counterexample array from manifest: {manifest_path}"
        )

    try:
        parsed = json.loads(match.group(1))
    except Exception as e:
        raise MalformedCounterexampleError(
            f"Malformed JSON in counterexample section of {manifest_path}: {e}"
        )

    if not isinstance(parsed, list) or len(parsed) == 0:
        raise MalformedCounterexampleError(
            f"Counterexample JSON in {manifest_path} must be a non-empty array of states."
        )

    return _normalize_counterexample_states(parsed)


def run_reproduction_test(test_path: Path) -> Dict[str, Any]:
    """Executes the test script in a separate Python process."""
    proc = subprocess.run(
        [sys.executable, str(test_path)],
        capture_output=True,
        text=True,
    )

    stdout = proc.stdout
    stderr = proc.stderr
    combined = f"{stdout}\n{stderr}".strip()

    parsed_json: Dict[str, Any] = {}
    for line in stdout.splitlines():
        if line.startswith("TRACEPROOF_JSON_RESULT:"):
            try:
                parsed_json = json.loads(line.replace("TRACEPROOF_JSON_RESULT:", "").strip())
            except Exception:
                pass

    assertion_failed = proc.returncode != 0 and ("AssertionError" in stderr or "AssertionError" in stdout)

    return {
        "returncode": proc.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "assertion_failed": assertion_failed,
        "json_data": parsed_json,
        "combined_output": combined,
    }


def confirm_bug(
    output_dir: str = ".traceproof-poc",
    target_source: Optional[str] = None,
) -> ReproductionResult:
    """
    Main entry point for Phase 7 Bug Confirmation.
    Derives runtime test harness 1-to-1 from TLC counterexample and verifies
    cryptographic provenance hashes before executing.
    """
    out_path = Path(output_dir).expanduser().resolve()
    manifest_path = out_path / "export" / "manifest.md"
    repro_dir = out_path / "reproduction"
    repro_dir.mkdir(parents=True, exist_ok=True)

    # 1. Parse and validate counterexample
    print("[reproducer] Inspecting TLC model checker counterexample artifact...")
    counterexample = parse_counterexample_from_manifest(manifest_path)

    # 2. Resolve target source path
    repo_root = Path(__file__).resolve().parents[2]
    target_rel = "examples/dist_lock/lock.py"
    if target_source:
        target_candidate = Path(target_source)
        if not target_candidate.is_absolute():
            target_candidate = (repo_root / target_candidate).resolve()
        if target_candidate.is_file():
            target_rel = str(target_candidate)
        elif target_candidate.is_dir():
            py_files = [p for p in target_candidate.glob("*.py") if p.name not in ("__init__.py",)]
            if py_files:
                target_rel = str(py_files[0])
            else:
                raise FileNotFoundError(f"No Python files found in target directory: {target_candidate}")
    target_path = Path(target_rel).resolve()
    if not target_path.exists():
        target_path = (repo_root / target_rel).resolve()
    if not target_path.exists():
        raise FileNotFoundError(f"Target source file does not exist: {target_path}")

    # 3. Resolve spec and config paths
    spec_path = out_path / "export" / "base.tla"
    if not spec_path.exists():
        spec_path = out_path / "model" / "base.tla"
    cfg_path = out_path / "export" / "base.cfg"
    if not cfg_path.exists():
        cfg_path = out_path / "model" / "base.cfg"

    # 4. Compute Non-Fungible Cryptographic Provenance Receipt
    print("[reproducer] Computing SHA-256 cryptographic provenance receipt...")
    provenance_receipt = compute_provenance(
        target_source=target_path,
        spec_path=spec_path,
        cfg_path=cfg_path,
        counterexample_data=counterexample,
    )

    # 5. Extract actions and select TargetAdapter
    actions = {step.get("action", "") for step in counterexample if step.get("action")}
    print(f"[reproducer] Selecting TargetAdapter for actions: {sorted(actions)}...")
    adapter = AdapterRegistry.get_adapter(target_path, actions)

    # 6. Synthesize deterministic test harness
    test_harness_path = repro_dir / "test_reproduce_bug.py"
    print(f"[reproducer] Synthesizing non-fungible test harness at {test_harness_path}...")
    harness_code, transition_table = build_dynamic_harness_script(
        target_path=target_path,
        repo_root=repo_root,
        receipt=provenance_receipt,
        counterexample=counterexample,
        adapter=adapter,
    )
    test_harness_path.write_text(harness_code, encoding="utf-8")

    # 7. Execute test against live target
    print(f"[reproducer] Executing test against live target ({target_path.name})...")
    exec_res = run_reproduction_test(test_harness_path)

    json_data = exec_res.get("json_data", {})
    violations_detected = json_data.get("violations_detected", 0)
    final_counter = json_data.get("final_counter")
    expected_counter = json_data.get("expected_counter")

    reproduced = exec_res["assertion_failed"] or (violations_detected > 0)
    verdict = "CONFIRMED_REAL_BUG" if reproduced else "UNREPRODUCIBLE"

    if reproduced:
        print(f"[reproducer]  \033[92m✔ BUG CONFIRMED\033[0m: Invariant violation deterministically reproduced! Violations detected: {violations_detected}")
    else:
        print(f"[reproducer]  \033[93m✖ UNREPRODUCIBLE\033[0m: Test passed without triggering invariant failure.")

    result_data = {
        "target": str(target_path),
        "target_source_hash": provenance_receipt.target_source_hash,
        "spec_hash": provenance_receipt.spec_hash,
        "cfg_hash": provenance_receipt.cfg_hash,
        "counterexample_hash": provenance_receipt.counterexample_hash,
        "reproduced": reproduced,
        "verdict": verdict,
        "violations_detected": violations_detected,
        "final_counter": final_counter,
        "expected_counter": expected_counter,
        "test_script_path": str(test_harness_path),
        "error_traceback": exec_res["stderr"].strip() or exec_res["stdout"].strip(),
        "counterexample_states": len(counterexample),
        "transition_mapping_table": transition_table,
        "details": json_data,
    }

    result_json_path = repro_dir / "reproduction_result.json"
    result_json_path.write_text(json.dumps(result_data, indent=2), encoding="utf-8")
    print(f"[reproducer] Provenance and reproduction result saved to {result_json_path}")

    return ReproductionResult(
        target=str(target_path),
        reproduced=reproduced,
        verdict=verdict,
        final_counter=final_counter,
        expected_counter=expected_counter,
        error_traceback=result_data["error_traceback"],
        test_script_path=str(test_harness_path),
        details=result_data,
        violations_detected=violations_detected,
        provenance=provenance_receipt.to_dict(),
        transition_mapping_table=transition_table,
        target_source_hash=provenance_receipt.target_source_hash,
        spec_hash=provenance_receipt.spec_hash,
        cfg_hash=provenance_receipt.cfg_hash,
        counterexample_hash=provenance_receipt.counterexample_hash,
    )
