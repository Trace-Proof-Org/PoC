"""Trace Validator: verifies model-code conformance using tla-mcp replay_scenario."""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from agents.spec_generator.verify import _connect_tla_rs
from agents.trace_validator.tracer import collect_counter_trace, ExecutionTrace


class ValidationResult(NamedTuple):
    passed: bool
    status: str
    events_count: int
    details: str
    scenario: str


def validate_trace_conformance(
    output_dir: str | Path = ".traceproof-poc",
    target_source: str | Path = "examples/dist_counter/counter.py",
) -> ValidationResult:
    """Collect runtime trace from target code and replay it against the generated TLA+ model."""
    out = Path(output_dir).expanduser().resolve()
    tla_path = out / "model" / "base.tla"
    cfg_path = out / "model" / "base.cfg"

    if not tla_path.exists():
        raise FileNotFoundError(f"Model file not found: {tla_path}. Run generate first.")

    # 1. Collect real execution trace from target code
    src_path = Path(target_source).resolve()
    print(f"[trace_validator] Collecting runtime execution trace from {src_path.name}...")
    trace = collect_counter_trace(src_path, num_nodes=2)
    scenario_text = trace.to_scenario()

    print(f"[trace_validator] Observed {len(trace.events)} code events:")
    for ev in trace.events:
        print(f"  Step {ev.step}: Node {ev.node} -> {ev.action} (counter={ev.counter})")

    # 2. Replay trace against TLA+ model via tla-mcp
    print("[trace_validator] Replaying trace against TLA+ model via tla-mcp (replay_scenario)...")
    client = _connect_tla_rs()
    try:
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
        passed = (status == "ok")

        if passed:
            print("[trace_validator] PASS: Formal model successfully admitted real execution trace!")
            print(f"[trace_validator] Model transitions verified: {len(data.get('trace', [])) - 1} steps")
        else:
            failure = data.get("failure", {})
            fail_msg = failure.get("message", text)
            print(f"[trace_validator] FAIL: Conformance gap detected: {fail_msg}")

        # 3. Write validation report
        val_dir = out / "validation"
        val_dir.mkdir(parents=True, exist_ok=True)
        report_path = val_dir / "trace-validation.md"

        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        events_str = "\n".join(
            f"- Step {ev.step}: Node {ev.node} -> {ev.action} (counter={ev.counter})"
            for ev in trace.events
        )

        report_content = f"""# Trace Validation Report

- **Timestamp**: {ts}
- **Target**: {src_path}
- **Model**: {tla_path}
- **Status**: {'CONFORMANCE VERIFIED' if passed else 'CONFORMANCE GAP DETECTED'}
- **Events Checked**: {len(trace.events)}

## Observed Code Execution Trace
{events_str}

## TLA+ Replay Scenario
```tla
{scenario_text.strip()}
```

## MCP Replay Outcome
- **Status**: {status}
- **Details**:
```json
{json.dumps(data, indent=2)}
```
"""
        report_path.write_text(report_content)
        print(f"[trace_validator] Report written to: {report_path}")

        return ValidationResult(
            passed=passed,
            status=status,
            events_count=len(trace.events),
            details=json.dumps(data, indent=2),
            scenario=scenario_text,
        )

    finally:
        client.stop()
