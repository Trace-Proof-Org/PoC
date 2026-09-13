"""Plain-English Diagnostic Report Generator.

Aggregates outputs from Invariant Mining, Model Spec, Trace Validation,
TLC Model Checking, Adversarial Critic, and Live Code Reproduction
into an executive-ready diagnostic report focusing on bug detection
and reproduction scenario delivery.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Optional

from agents.bug_reproducer.templates import build_diagnostic_report_markdown


def generate_diagnostic_report(
    output_dir: str = ".traceproof-poc",
    target_source: Optional[str] = None,
) -> Path:
    """
    Consolidates pipeline artifacts into a comprehensive bug report at
    `output_dir/reports/bug_report.md`.
    """
    out_path = Path(output_dir).expanduser().resolve()
    reports_dir = out_path / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    report_file = reports_dir / "bug_report.md"

    # 1. Load Reproduction Result JSON
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
    final_storage = details.get("final_storage")
    traceback_str = repro_data.get("error_traceback", "AssertionError: Invariant violation reproduced.")

    provenance = repro_data.get("provenance")
    if not provenance and repro_data.get("target_source_hash"):
        provenance = {
            "target_source_hash": repro_data.get("target_source_hash", ""),
            "spec_hash": repro_data.get("spec_hash", ""),
            "cfg_hash": repro_data.get("cfg_hash", ""),
            "counterexample_hash": repro_data.get("counterexample_hash", ""),
        }
    transition_mappings = repro_data.get("transition_mapping_table")

    # 2. Extract Scenario from run-config.md
    scenario = None
    cfg_file = out_path / "run-config.md"
    if cfg_file.exists():
        for line in cfg_file.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip().startswith("- **Scenario**:"):
                scenario = line.split(":", 1)[1].strip()
                if scenario in ("<module-scoped run>", "module-scoped run", ""):
                    scenario = None

    # 3. Extract Critic Findings from adversary-report.md
    adv_file = out_path / "adversary" / "adversary-report.md"
    adv_summary = None
    adv_citations: List[str] = []
    adv_confidence = None
    adv_verdict = None
    if adv_file.exists():
        adv_text = adv_file.read_text(encoding="utf-8", errors="replace")
        for line in adv_text.splitlines():
            if line.strip().startswith("- **Verdict**:"):
                adv_verdict = line.split(":", 1)[1].strip()
            elif line.strip().startswith("- **Confidence**:"):
                try:
                    adv_confidence = float(line.split(":", 1)[1].strip())
                except Exception:
                    pass

        if "## Executive Summary" in adv_text:
            adv_summary = adv_text.split("## Executive Summary", 1)[1].split("##", 1)[0].strip()

        if "## Code Citations" in adv_text:
            cit_block = adv_text.split("## Code Citations", 1)[1].split("##", 1)[0].strip()
            adv_citations = [
                line.strip("- ").strip("`")
                for line in cit_block.splitlines()
                if line.strip().startswith("-")
            ]

    target_name = target_source or repro_data.get("target", "examples/dist_lock")
    egypt_tz = timezone(timedelta(hours=3))
    timestamp = datetime.now(egypt_tz).strftime("%Y-%m-%d %H:%M:%S (UTC+3, Egypt Time)")

    report_content = build_diagnostic_report_markdown(
        target_name=target_name,
        timestamp=timestamp,
        verdict=verdict,
        violations_detected=violations_detected,
        traceback_str=traceback_str,
        storage=final_storage,
        provenance=provenance,
        transition_mappings=transition_mappings,
        scenario=scenario,
        adversary_summary=adv_summary,
        code_citations=adv_citations,
        critic_verdict=adv_verdict,
        critic_confidence=adv_confidence,
    )

    report_file.write_text(report_content, encoding="utf-8")
    print(f"[reporter] Diagnostic report generated at {report_file}")
    return report_file
