"""Deterministic Bug Reproduction Agent.

Parses formal verification counterexamples from TLC and synthesizes a
deterministic runtime test harness to reproduce the race condition directly
on the real Python codebase.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from agents.bug_reproducer.templates import build_harness_script


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


def parse_counterexample_from_manifest(manifest_path: Path) -> Optional[List[Dict[str, Any]]]:
    """Extract JSON counterexample trace from export/manifest.md."""
    if not manifest_path.exists():
        return None
    content = manifest_path.read_text(encoding="utf-8")
    if "COUNTEREXAMPLE FOUND" not in content:
        return None

    idx = content.find("### Counterexample")
    if idx == -1:
        return None
    sub = content[idx:]
    match = re.search(r"(\[\s*\{.*\}\s*\])", sub, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except Exception:
        return None


def generate_test_harness(target_rel_path: str, repo_root: Path, out_file: Path) -> None:
    """Synthesizes a deterministic Python test script."""
    out_file.parent.mkdir(parents=True, exist_ok=True)
    harness_code = build_harness_script(target_rel_path=target_rel_path, repo_root=repo_root)
    out_file.write_text(harness_code, encoding="utf-8")


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
    """
    out_path = Path(output_dir)
    manifest_path = out_path / "export" / "manifest.md"
    repro_dir = out_path / "reproduction"
    repro_dir.mkdir(parents=True, exist_ok=True)

    print("[reproducer] Inspecting model checker counterexample...")
    counterexample = parse_counterexample_from_manifest(manifest_path)

    target_rel = "examples/dist_lock/lock.py"
    if target_source:
        target_path = Path(target_source)
        if target_path.is_file():
            target_rel = str(target_path)
        elif target_path.is_dir():
            py_files = [p for p in target_path.glob("*.py") if p.name not in ("__init__.py",)]
            if py_files:
                target_rel = str(py_files[0])

    test_harness_path = repro_dir / "test_reproduce_bug.py"
    repo_root = Path(__file__).resolve().parents[2]

    print(f"[reproducer] Synthesizing deterministic test harness at {test_harness_path}...")
    generate_test_harness(
        target_rel_path=target_rel,
        repo_root=repo_root,
        out_file=test_harness_path,
    )

    print(f"[reproducer] Executing test against live target ({target_rel})...")
    exec_res = run_reproduction_test(test_harness_path)

    json_data = exec_res.get("json_data", {})
    violations_detected = json_data.get("violations_detected", 0)
    final_counter = json_data.get("final_counter")
    expected_counter = json_data.get("expected_counter")

    reproduced = exec_res["assertion_failed"] or (violations_detected > 0)
    verdict = "CONFIRMED_REAL_BUG" if reproduced else "UNREPRODUCIBLE"

    if reproduced:
        print(f"[reproducer] [92m✔ BUG CONFIRMED[0m: Mutual exclusion broken! Violations detected: {violations_detected}")
    else:
        print(f"[reproducer] [93m✖ UNREPRODUCIBLE[0m: Test passed without triggering invariant failure.")

    result_data = {
        "target": target_rel,
        "reproduced": reproduced,
        "verdict": verdict,
        "violations_detected": violations_detected,
        "final_counter": final_counter,
        "expected_counter": expected_counter,
        "test_script_path": str(test_harness_path),
        "error_traceback": exec_res["stderr"].strip() or exec_res["stdout"].strip(),
        "counterexample_states": len(counterexample) if counterexample else 0,
        "details": json_data,
    }

    result_json_path = repro_dir / "reproduction_result.json"
    result_json_path.write_text(json.dumps(result_data, indent=2), encoding="utf-8")
    print(f"[reproducer] Reproduction result saved to {result_json_path}")

    return ReproductionResult(
        target=target_rel,
        reproduced=reproduced,
        verdict=verdict,
        final_counter=final_counter,
        expected_counter=expected_counter,
        error_traceback=result_data["error_traceback"],
        test_script_path=str(test_harness_path),
        details=result_data,
        violations_detected=violations_detected,
    )
