"""Trace Validator Agent: verifies model-code conformance using tla-mcp replay_scenario and self-repair."""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple, Any

from agents.spec_generator.verify import _connect_tla_rs
from agents.spec_generator.generate import _call_llm, _split_llm_output
from agents.trace_validator.tracer import collect_execution_trace, ExecutionTrace, _resolve_target_file
from agents.trace_validator.prompts import build_trace_mapping_prompt, build_conformance_repair_prompt


class ValidationResult(NamedTuple):
    passed: bool
    status: str
    events_count: int
    details: str
    scenario: str
    repairs_used: int


def _parse_run_config(out: Path) -> dict[str, str]:
    cfg_path = out / "run-config.md"
    if not cfg_path.exists():
        return {}
    res = {}
    for line in cfg_path.read_text().splitlines():
        m = re.match(r"^-\s+\*\*([^*]+)\*\*:\s*(.*)", line)
        if m:
            res[m.group(1).strip()] = m.group(2).strip()
    return res


def validate_trace_conformance(
    output_dir: str | Path = ".traceproof-poc",
    target_source: str | Path = "examples/dist_lock",
) -> ValidationResult:
    out = Path(output_dir).expanduser().resolve()
    tla_path = out / "model" / "base.tla"
    cfg_path = out / "model" / "base.cfg"

    if not tla_path.exists():
        raise FileNotFoundError(f"Model file not found: {tla_path}. Run generate first.")

    cfg_data = _parse_run_config(out)
    provider = cfg_data.get("Provider", "gemini")
    model_strong = cfg_data.get("Model (strong / drafting)", "gemini-3.7-flash")
    repair_cap = int(cfg_data.get("Self-repair cap", "5"))

    # 1. Harvest real physical execution trace from target code
    src_path = Path(target_source).resolve()
    print(f"[trace_validator] Collecting runtime execution trace from {src_path.name}...")
    trace = collect_execution_trace(src_path, num_nodes=2)
    print(f"[trace_validator] Observed {len(trace.events)} code events:")
    for ev in trace.events:
        print(f"  Step {ev.step}: Worker {ev.worker} -> {ev.action}")

    # 2. Synthesize TLA+ replay scenario
    target_file = _resolve_target_file(src_path)
    target_code = target_file.read_text()
    tla_text = tla_path.read_text()

    events_data = [
        {"step": ev.step, "worker": ev.worker, "action": ev.action, "details": ev.details}
        for ev in trace.events
    ]

    scenario_text = ""
    try:
        mapping_prompt = build_trace_mapping_prompt(target_code, tla_text, events_data)
        llm_scenario = _call_llm(provider, model_strong, mapping_prompt)
        if llm_scenario and "step:" in llm_scenario:
            lines = [line.strip() for line in llm_scenario.splitlines() if line.strip().startswith("step:")]
            if len(lines) >= len(trace.events):
                scenario_text = "\n".join(lines) + "\n"
    except Exception as e:
        print(f"[trace_validator] LLM mapping notice: {e}")

    if not scenario_text:
        scenario_text = trace.to_scenario()

    print("[trace_validator] Replay scenario synthesized:")
    for s_line in scenario_text.strip().splitlines():
        print(f"  {s_line}")

    # 3. Replay against TLA+ model via tla-mcp with Self-Repair Loop
    print("[trace_validator] Replaying trace against TLA+ model via tla-mcp (replay_scenario)...")
    client = _connect_tla_rs()

    passed = False
    status = "unknown"
    repairs_used = 0
    data: dict[str, Any] = {}

    try:
        while repairs_used <= repair_cap:
            client._req_id += 1
            req = {
                "jsonrpc": "2.0",
                "id": client._req_id,
                "method": "tools/call",
                "params": {
                    "name": "replay_scenario",
                    "arguments": {
                        "spec_path": str(tla_path),
                        "config_path": str(cfg_path) if cfg_path.exists() else None,
                        "scenario": scenario_text,
                    },
                },
            }
            client._send(req)
            resp = client._recv()

            content = resp.get("result", {}).get("content", [])
            text = content[0].get("text", "") if content else ""
            try:
                data = json.loads(text)
            except Exception:
                data = {"raw": text}

            status = data.get("status", "")
            if status == "ok":
                passed = True
                print("[trace_validator] PASS: Formal model successfully admitted real execution trace!")
                transitions_verified = len(data.get("trace", [])) - 1
                print(f"[trace_validator] Model transitions verified: {transitions_verified} steps")
                break

            failure = data.get("failure", {})
            fail_msg = failure.get("message", text)
            print(f"[trace_validator] FAIL: Conformance gap detected: {fail_msg}")

            if repairs_used >= repair_cap:
                print(f"[trace_validator] Self-repair cap reached ({repair_cap} attempts). Halting.")
                break

            repairs_used += 1
            print(f"[trace_validator] Self-repair attempt {repairs_used}/{repair_cap} using {model_strong}...")

            repair_prompt = build_conformance_repair_prompt(tla_text, scenario_text, failure)
            repaired_tla = _call_llm(provider, model_strong, repair_prompt)
            if not repaired_tla:
                print("[trace_validator] Repair LLM returned no content; retrying...")
                continue

            cleaned_tla, extra_cfg = _split_llm_output(repaired_tla)
            tla_text = cleaned_tla
            tla_path.write_text(tla_text)
            if extra_cfg:
                cfg_path.write_text(extra_cfg)

        # 4. Write validation report
        val_dir = out / "validation"
        val_dir.mkdir(parents=True, exist_ok=True)
        report_path = val_dir / "trace-validation.md"

        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        events_str = "\n".join(
            f"- Step {ev.step}: Worker {ev.worker} -> {ev.action} ({ev.details})"
            for ev in trace.events
        )

        status_label = "CONFORMANCE VERIFIED" if passed else "CONFORMANCE GAP DETECTED"
        details_json = json.dumps(data, indent=2)

        report_lines = [
            "# Trace Validation Report\n",
            f"- **Timestamp**: {ts}",
            f"- **Target**: {src_path}",
            f"- **Model**: {tla_path}",
            f"- **Status**: {status_label}",
            f"- **Events Checked**: {len(trace.events)}",
            f"- **Self-Repair Attempts Used**: {repairs_used} / {repair_cap}\n",
            "## Observed Code Execution Trace",
            events_str,
            "\n## TLA+ Replay Scenario",
            "```tla",
            scenario_text.strip(),
            "```\n",
            "## MCP Replay Outcome",
            f"- **Status**: {status}",
            "- **Details**:",
            "```json",
            details_json,
            "```",
        ]
        report_path.write_text("\n".join(report_lines))
        print(f"[trace_validator] Report written to: {report_path}")

        return ValidationResult(
            passed=passed,
            status=status,
            events_count=len(trace.events),
            details=details_json,
            scenario=scenario_text,
            repairs_used=repairs_used,
        )

    finally:
        client.stop()
