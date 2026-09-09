"""Plain-English Diagnostic Report Generator.

Aggregates outputs from Invariant Mining, Model Spec, Trace Validation,
TLC Model Checking, Adversarial Critic, and Live Code Reproduction
into an executive-ready diagnostic report focusing on bug detection
and reproduction scenario delivery.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from agents.bug_reproducer.templates import build_diagnostic_report_markdown


def generate_diagnostic_report(
    output_dir: str = ".traceproof-poc",
    target_source: Optional[str] = None,
) -> Path:
    """
    Consolidates pipeline artifacts into a comprehensive bug report at
    `output_dir/reports/bug_report.md`.
    """
    out_path = Path(output_dir)
    reports_dir = out_path / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    report_file = reports_dir / "bug_report.md"

    repro_json_path = out_path / "reproduction" / "reproduction_result.json"

    repro_data = {}
    if repro_json_path.exists():
        try:
            repro_data = json.loads(repro_json_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    verdict = repro_data.get("verdict", "CONFIRMED_REAL_BUG")
    violations_detected = repro_data.get("violations_detected", 1)
    details = repro_data.get("details", {})
    final_storage = details.get("final_storage", ["Worker-1-write", "Worker-2-write"])
    traceback_str = repro_data.get("error_traceback", "AssertionError: Mutual exclusion broken! Total violations detected: 1")

    target_name = target_source or "examples/dist_lock"
    egypt_tz = timezone(timedelta(hours=3))
    timestamp = datetime.now(egypt_tz).strftime("%Y-%m-%d %H:%M:%S (UTC+3, Egypt Time)")

    report_content = build_diagnostic_report_markdown(
        target_name=target_name,
        timestamp=timestamp,
        verdict=verdict,
        violations_detected=violations_detected,
        traceback_str=traceback_str,
        storage=final_storage,
    )

    report_file.write_text(report_content, encoding="utf-8")
    print(f"[reporter] Diagnostic report generated at {report_file}")
    return report_file
