"""Adversarial Agent (Critic): challenges model assumptions, invariants, and counterexamples."""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from shared.setup import (
    credential_present as _credential_present,
    get_api_key as _get_api_key,
    get_base_url as _get_base_url,
)
from agents.adversary.prompts import build_adversary_prompt, FALLBACK_CRITIQUE


@dataclass
class AdversarialResult:
    verdict: str  # CONFIRMED_BUG_CANDIDATE | MODEL_REPAIR_NEEDED | INVARIANT_REFINEMENT_NEEDED
    confidence: float
    summary: str
    invariant_critique: str
    model_fidelity_critique: str
    concurrency_critique: str
    code_citations: list[str] = field(default_factory=list)
    reproduction_guidance: str = ""
    raw_response: str = ""


def _call_llm_json(provider: str, model: str, prompt: str) -> dict | None:
    api_key = _get_api_key(provider)
    if not api_key:
        return None

    base_url = _get_base_url(provider)
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 2048,
        "temperature": 0.1,
    }).encode()

    try:
        if provider == "anthropic":
            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages",
                data=json.dumps({
                    "model": model,
                    "max_tokens": 2048,
                    "messages": [{"role": "user", "content": prompt}],
                }).encode(),
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = json.loads(resp.read())
            text = data["content"][0]["text"]
        else:
            req = urllib.request.Request(
                f"{base_url.rstrip('/')}/chat/completions",
                data=payload,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = json.loads(resp.read())
            text = data["choices"][0]["message"]["content"]

        # Parse JSON
        text = re.sub(r"^```[a-z]*\n?", "", text.strip(), flags=re.MULTILINE)
        text = re.sub(r"```$", "", text.strip(), flags=re.MULTILINE)
        try:
            return json.loads(text.strip())
        except Exception:
            m = re.search(r"\{.*\}", text, re.DOTALL)
            if m:
                return json.loads(m.group())
    except Exception as e:
        print(f"  [adversary] warning: LLM critique call failed: {e}", file=sys.stderr)

    return None


def run_adversary_critique(
    output_dir: str | Path = ".traceproof-poc",
    target_source: str | Path = "examples/dist_lock",
    model: str | None = None,
) -> AdversarialResult:
    """Run the Adversarial Agent to critically evaluate the formal counterexample."""
    out = Path(output_dir).expanduser().resolve()
    manifest_path = out / "export" / "manifest.md"
    tla_path = out / "export" / "base.tla"
    if not tla_path.exists():
        tla_path = out / "model" / "base.tla"

    src_path = Path(target_source).resolve()

    if not tla_path.exists():
        raise FileNotFoundError(f"Model file not found: {tla_path}")
    if not src_path.exists():
        raise FileNotFoundError(f"Source file not found: {src_path}")

    # Read artifacts
    if src_path.is_dir():
        py_files = sorted(src_path.glob("*.py"))
        source_code = "\n\n".join(
            f"# File: {f.name}\n" + f.read_text(errors="replace") for f in py_files
        ) if py_files else "(no python files found in directory)"
    else:
        source_code = src_path.read_text(errors="replace")
    tla_spec = tla_path.read_text(errors="replace")

    counterexample = "No counterexample recorded."
    if manifest_path.exists():
        text = manifest_path.read_text()
        if "### Counterexample" in text:
            counterexample = text.split("### Counterexample", 1)[1].split("## Handoff")[0].strip()

    # Read run config for provider and model
    cfg_file = out / "run-config.md"
    provider = "local"
    model_strong = ""
    if cfg_file.exists():
        for line in cfg_file.read_text().splitlines():
            if "**Provider**:" in line:
                provider = line.split(":", 1)[1].strip()
            elif "**Model (strong / drafting)**:" in line:
                model_strong = line.split(":", 1)[1].strip()

    active_model = model or model_strong

    print("[adversary] Running Adversarial Agent (Critic)...")
    print(f"[adversary] Attacking counterexample with provider: {provider}, model: {active_model or 'default'}...")

    prompt = build_adversary_prompt(
        source_name=src_path.name,
        source_code=source_code,
        tla_spec=tla_spec,
        counterexample=counterexample,
    )

    res_dict = None
    if _credential_present(provider) and active_model:
        res_dict = _call_llm_json(provider, active_model, prompt)

    if not res_dict:
        res_dict = FALLBACK_CRITIQUE

    verdict = res_dict.get("verdict", "CONFIRMED_BUG_CANDIDATE")
    confidence = float(res_dict.get("confidence", 0.9))
    summary = str(res_dict.get("summary", ""))
    inv_crit = str(res_dict.get("invariant_critique", ""))
    mod_crit = str(res_dict.get("model_fidelity_critique", ""))
    conc_crit = str(res_dict.get("concurrency_critique", ""))
    citations = [str(c) for c in res_dict.get("code_citations", [])]
    guidance = str(res_dict.get("reproduction_guidance", ""))

    print(f"\n[adversary] Adversary Verdict: {verdict} (Confidence: {confidence:.2f})")
    print(f"[adversary] Summary: {summary}")

    # Write report
    adv_dir = out / "adversary"
    adv_dir.mkdir(parents=True, exist_ok=True)
    report_path = adv_dir / "adversary-report.md"

    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    citations_str = "\n".join(f"- `{c}`" for c in citations) or "- (none cited)"

    content = f"""# Adversarial Refinement Report

- **Generated**: {ts}
- **Verdict**: {verdict}
- **Confidence**: {confidence:.2f}
- **Target**: `{src_path.name}`

## Executive Summary
{summary}

## 1. Invariant Soundness Critique
{inv_crit}

## 2. Model Fidelity Critique
{mod_crit}

## 3. Concurrency & Interleaving Feasibility
{conc_crit}

## Code Citations
{citations_str}

## Handoff & Reproduction Guidance
{guidance}
"""
    report_path.write_text(content)
    print(f"[adversary] Report written to: {report_path}")

    return AdversarialResult(
        verdict=verdict,
        confidence=confidence,
        summary=summary,
        invariant_critique=inv_crit,
        model_fidelity_critique=mod_crit,
        concurrency_critique=conc_crit,
        code_citations=citations,
        reproduction_guidance=guidance,
        raw_response=json.dumps(res_dict, indent=2),
    )
