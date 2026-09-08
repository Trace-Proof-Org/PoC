"""
Model Generation
Reads run-config.md + knowledge/_index.md, selects relevant modules,
drafts model/base.tla + model/base.cfg, appends to model/generation-log.md.

Strong model does the TLA+ draft; cheap model does a local lint-fix if needed.
Falls back to a deterministic structural spec when no LLM key is available.
"""

from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

RUN_CONFIG    = "run-config.md"
INDEX_FILE    = "knowledge/_index.md"
LOG_FILE      = "model/generation-log.md"
BASE_TLA      = "model/base.tla"
BASE_CFG      = "model/base.cfg"


# Errors
class GenerateError(Exception):
    pass


# run-config.md reader (reuse same pattern as index_phase)
def _parse_run_config(out: Path) -> dict[str, str]:
    cfg = out / RUN_CONFIG
    if not cfg.exists():
        raise GenerateError(f"No run-config.md under {out}. Run 'traceproof-poc run' first.")
    data: dict[str, str] = {}
    for line in cfg.read_text().splitlines():
        m = re.match(r"^-\s+\*\*(.+?)\*\*:\s*(.+)$", line)
        if m:
            data[m.group(1).strip()] = m.group(2).strip()
    return data


# Knowledge index reader
def _load_index(out: Path) -> dict[str, dict[str, str]]:
    """Returns {module: {hash, cache_file, ts}}"""
    idx = out / INDEX_FILE
    if not idx.exists():
        raise GenerateError(
            f"No knowledge/_index.md under {out}. Run 'traceproof-poc index' first."
        )
    modules: dict[str, dict[str, str]] = {}
    for line in idx.read_text().splitlines():
        m = re.match(r"^\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*([a-f0-9]+)\s*\|\s*(.+?)\s*\|$", line)
        if m and m.group(1) not in ("Module", "---"):
            modules[m.group(1)] = {
                "cache_file": m.group(2).strip(),
                "hash":       m.group(3).strip(),
                "ts":         m.group(4).strip(),
            }
    return modules


def _read_knowledge_file(out: Path, cache_path: str) -> str:
    p = out / cache_path
    if p.exists():
        return p.read_text()
    return ""


# Module selection
CONCURRENCY_TOKENS = re.compile(
    r"\b(lock|mutex|semaphore|atomic|thread|asyncio|await|async|"
    r"concurrent|queue|channel|barrier|condition|race)\b",
    re.IGNORECASE,
)

def _score_module(module: str, knowledge_text: str, scenario_tokens: set[str]) -> int:
    """Relevance score: higher = more relevant to scenario."""
    score = 0
    kl = knowledge_text.lower()
    # scenario token hits (in Summary/State/Invariants sections, not Scenario Relevance)
    body = re.split(r"## Scenario Relevance", kl)[0]
    for tok in scenario_tokens:
        score += body.count(tok) * 2
    # concurrency bonus
    if CONCURRENCY_TOKENS.search(knowledge_text):
        score += 5
    return score


def _select_modules(
    index: dict[str, dict[str, str]],
    out: Path,
    scenario: str | None,
) -> list[tuple[str, str]]:
    """
    Returns [(module_name, knowledge_text)], up to 3 if scenario, else 1.
    """
    entries = []
    for mod, info in index.items():
        text = _read_knowledge_file(out, info["cache_file"])
        entries.append((mod, text, info))

    if scenario:
        tokens = set(re.findall(r"\b\w{3,}\b", scenario.lower()))
        scored = sorted(entries, key=lambda e: _score_module(e[0], e[1], tokens), reverse=True)
        selected = scored[:3]
    else:
        # No scenario: prefer module with concurrency signals, else first
        with_conc = [e for e in entries if CONCURRENCY_TOKENS.search(e[1])]
        selected  = [with_conc[0]] if with_conc else [entries[0]] if entries else []

    return [(mod, text) for mod, text, _ in selected]


# Caching check (skip if hashes unchanged)
def _generation_cache_key(selected: list[tuple[str, str]], index: dict[str, dict[str, str]]) -> str:
    """Stable key: sorted module names + their hashes."""
    parts = []
    for mod, _ in sorted(selected):
        h = index.get(mod, {}).get("hash", "")
        parts.append(f"{mod}:{h}")
    return "|".join(parts)


def _read_last_cache_key(out: Path) -> str:
    log = out / LOG_FILE
    if not log.exists():
        return ""
    for line in reversed(log.read_text().splitlines()):
        m = re.match(r"^cache-key:\s*(.+)$", line)
        if m:
            return m.group(1).strip()
    return ""


# LLM draft

# Credential resolution is centralised in setup_phase.
from setup_phase import credential_present as _credential_present, get_api_key as _get_api_key, get_base_url as _get_base_url


def _build_prompt(
    selected: list[tuple[str, str]],
    scenario: str | None,
) -> str:
    knowledge_block = ""
    for mod, text in selected:
        knowledge_block += f"\n\n--- knowledge/{mod}.md ---\n{text[:3000]}"

    scenario_line = (
        f'Scenario: "{scenario}"' if scenario
        else f'Module-scoped run (no scenario): model the primary module "{selected[0][0]}".'
    )

    return f"""You are a TLA+/PlusCal spec assistant. Draft a TLA+ specification for model checking.

{scenario_line}

Knowledge cache:
{knowledge_block}

Requirements:
1. Use PlusCal algorithm syntax inside a TLA+ MODULE named "base".
2. Include Init and Next predicates (or PlusCal translates them).
3. Define at least one INVARIANT for each candidate invariant in the knowledge files.
4. Add inline comments citing the knowledge file for every non-trivial guard, e.g.:
   \\* Source: knowledge/{selected[0][0]}.md, "<invariant text>"
5. Keep the spec small and checkable, a PoC, not a complete model.
6. Output ONLY the raw TLA+ text (no markdown fences, no explanation).
   Start with: ---- MODULE base ----

Also output a base.cfg after a line "====CFG====":
SPECIFICATION Spec
INVARIANT <InvariantName1>
INVARIANT <InvariantName2>
CONSTANTS
  <ConstantName> = <small_value>
"""


def _call_llm(provider: str, model: str, prompt: str) -> str | None:
    api_key = _get_api_key(provider)
    if not api_key:
        return None

    base_url = _get_base_url(provider)

    try:
        if provider == "anthropic":
            return _call_anthropic(api_key, model, prompt)
        else:
            return _call_openai_compat(api_key, base_url, model, prompt)
    except Exception as e:
        print(f"  warning: LLM call failed: {e}", file=sys.stderr)
        return None


def _call_openai_compat(api_key: str, base_url: str, model: str, prompt: str) -> str:
    import urllib.request, json
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 2048,
        "temperature": 0,
    }).encode()
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = json.loads(resp.read())
    return data["choices"][0]["message"]["content"]


def _call_anthropic(api_key: str, model: str, prompt: str) -> str:
    import urllib.request, json
    payload = json.dumps({
        "model": model,
        "max_tokens": 2048,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = json.loads(resp.read())
    return data["content"][0]["text"]


# Parse LLM output → tla + cfg
def _split_llm_output(raw: str) -> tuple[str, str]:
    """Split raw LLM output into (tla_text, cfg_text) on '====CFG===='."""
    if "====CFG====" in raw:
        parts = raw.split("====CFG====", 1)
        return parts[0].strip(), parts[1].strip()
    return raw.strip(), ""


# Local lint (no MCP)
LINT_CHECKS = [
    (r"MODULE\s+\w+", "Missing MODULE declaration"),
    (r"\bInit\b",     "Missing Init predicate"),
    (r"\bNext\b",     "Missing Next predicate"),
]

def _local_lint(tla_text: str) -> list[str]:
    errors = []
    for pattern, msg in LINT_CHECKS:
        if not re.search(pattern, tla_text):
            errors.append(msg)
    return errors


def _lint_fix_with_llm(
    tla_text: str,
    errors: list[str],
    provider: str,
    model: str,
) -> str | None:
    fix_prompt = f"""The following TLA+ spec has lint errors. Fix them and return ONLY the corrected TLA+ text.

Errors:
{chr(10).join(f'- {e}' for e in errors)}

Spec:
{tla_text}
"""
    return _call_llm(provider, model, fix_prompt)


# Deterministic fallback spec
def _fallback_spec(selected: list[tuple[str, str]], scenario: str | None) -> tuple[str, str]:
    """Minimal valid TLA+ spec generated from knowledge text, no LLM needed."""
    mod_name = selected[0][0] if selected else "unknown"
    scenario_str = scenario if scenario else f"module-scoped: {mod_name}"

    # Pull invariant bullets from knowledge files
    invariants: list[tuple[str, str]] = []  # (label, source)
    for mod, text in selected:
        section = re.search(r"## Candidate Invariants\n(.+?)(?=\n##|\Z)", text, re.DOTALL)
        if section:
            for line in section.group(1).strip().splitlines():
                line = line.strip().lstrip("-").strip()
                if line and not line.startswith("("):
                    label = "Inv_" + re.sub(r"\W+", "_", line[:40]).strip("_")
                    invariants.append((label, f"knowledge/{mod}.md, {line[:80]}"))

    if not invariants:
        invariants = [("Inv_Placeholder", f"knowledge/{mod_name}.md, placeholder invariant (structural fallback)")]

    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")

    inv_defs = "\n".join(
        f"\\* Source: {src}\n{label} == TRUE  \\* TODO: replace with real predicate"
        for label, src in invariants[:4]
    )
    inv_names = "\n".join(f"INVARIANT {label}" for label, _ in invariants[:4])
    knowledge_list = "\n".join(f"   - knowledge/{mod}.md" for mod, _ in selected)

    tla = f"""---- MODULE base ----
(* Generated by traceproof-poc, Phase 3 (Model Generation)
   Scenario: {scenario_str}
   Derived from knowledge files:
{knowledge_list}
   Generated: {ts}
   Self-repair attempts this run: 0
   Note: structural fallback (no LLM key), invariant bodies are placeholders
*)
EXTENDS Naturals, Sequences, TLC

CONSTANTS MaxSteps

VARIABLES pc, step

TypeOK ==
    /\\ pc \\in {{"init", "running", "done", "error"}}
    /\\ step \\in 0..MaxSteps

{inv_defs}

Init ==
    /\\ pc = "init"
    /\\ step = 0

Next ==
    \\/ /\\ pc = "init"
       /\\ pc' = "running"
       /\\ step' = step
    \\/ /\\ pc = "running"
       /\\ step < MaxSteps
       /\\ pc' = "done"
       /\\ step' = step + 1
    \\/ /\\ pc = "running"
       /\\ step >= MaxSteps
       /\\ pc' = "error"
       /\\ step' = step

Spec == Init /\\ [][Next]_<<pc, step>>

====
"""

    cfg = f"""SPECIFICATION Spec
INVARIANT TypeOK
{inv_names}
CONSTANTS
  MaxSteps = 5
"""
    return tla, cfg


# File writers
def _write_model(out: Path, tla: str, cfg: str) -> tuple[Path, Path]:
    model_dir = out / "model"
    model_dir.mkdir(parents=True, exist_ok=True)
    tla_path = model_dir / "base.tla"
    cfg_path = model_dir / "base.cfg"
    tla_path.write_text(tla)
    cfg_path.write_text(cfg)
    return tla_path, cfg_path


def _append_log(
    out: Path,
    scenario: str | None,
    selected_mods: list[str],
    invariants_summary: str,
    cache_key: str,
    used_llm: bool,
    lint_errors: list[str],
    lint_fixed: bool,
) -> None:
    ts    = datetime.now(timezone.utc).isoformat(timespec="seconds")
    entry = f"""
---
## Generation entry, {ts}
- Scenario: {scenario if scenario else 'none, module-scoped run'}
- Modules used: {', '.join(selected_mods)}
- LLM used: {'yes' if used_llm else 'no (structural fallback)'}
- Lint errors before fix: {lint_errors if lint_errors else 'none'}
- Lint fixed: {lint_fixed}
- Invariants: {invariants_summary}
cache-key: {cache_key}
"""
    log_path = out / LOG_FILE
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a") as f:
        f.write(entry)


# Public API
def generate_run(output_dir: str | Path = ".traceproof-poc") -> tuple[Path, Path]:
    """
    Phase 3 entry point. Returns (base.tla path, base.cfg path).
    Raises GenerateError on failure.
    """
    out = Path(output_dir).expanduser().resolve()
    cfg_data  = _parse_run_config(out)
    index     = _load_index(out)

    scenario_raw = cfg_data.get("Scenario", "none, module-scoped run")
    scenario     = None if "none" in scenario_raw.lower() else scenario_raw
    provider     = cfg_data.get("Provider", "local")
    model_strong = cfg_data.get("Model (strong / drafting)", "")
    model_cheap  = cfg_data.get("Model (cheap / classification)", "")

    if not index:
        raise GenerateError("knowledge/_index.md is empty. Re-run 'traceproof-poc index'.")

    selected = _select_modules(index, out, scenario)
    if not selected:
        raise GenerateError("No modules available for generation.")

    cache_key = _generation_cache_key(selected, index)
    last_key  = _read_last_cache_key(out)
    tla_path  = out / BASE_TLA

    if cache_key == last_key and tla_path.exists():
        print(f"skipped generate (knowledge hashes unchanged), {tla_path}")
        return tla_path, out / BASE_CFG

    selected_mods = [mod for mod, _ in selected]
    print(f"Generating model from: {', '.join(selected_mods)}")

    use_llm  = _credential_present(provider)
    tla, cfg = "", ""
    used_llm = False
    lint_errors: list[str] = []
    lint_fixed  = False

    if use_llm and model_strong:
        prompt = _build_prompt(selected, scenario)
        raw    = _call_llm(provider, model_strong, prompt)
        if raw:
            tla, cfg = _split_llm_output(raw)
            used_llm = True

            # Local lint
            lint_errors = _local_lint(tla)
            if lint_errors and model_cheap:
                print(f"  lint errors: {lint_errors}, attempting fix with cheap model")
                fixed = _lint_fix_with_llm(tla, lint_errors, provider, model_cheap)
                if fixed:
                    tla, extra_cfg = _split_llm_output(fixed)
                    if extra_cfg:
                        cfg = extra_cfg
                    remaining = _local_lint(tla)
                    lint_fixed = len(remaining) < len(lint_errors)

    if not tla:
        # Structural fallback
        tla, cfg = _fallback_spec(selected, scenario)

    # Fill cfg if LLM forgot the ====CFG==== separator
    if not cfg:
        inv_names = re.findall(r"^(\w+Inv\w*|Inv_\w+|TypeOK)\s*==", tla, re.MULTILINE)
        inv_lines = "\n".join(f"INVARIANT {n}" for n in inv_names[:4]) or "INVARIANT TypeOK"
        cfg = f"SPECIFICATION Spec\n{inv_lines}\nCONSTANTS\n  MaxSteps = 5\n"

    tla_p, cfg_p = _write_model(out, tla, cfg)

    # Invariants summary for the log
    inv_names_found = re.findall(r"^(Inv_\w+|TypeOK)\s*==", tla, re.MULTILINE)
    inv_summary = ", ".join(inv_names_found) if inv_names_found else "(none detected)"

    _append_log(
        out=out,
        scenario=scenario,
        selected_mods=selected_mods,
        invariants_summary=inv_summary,
        cache_key=cache_key,
        used_llm=used_llm,
        lint_errors=lint_errors,
        lint_fixed=lint_fixed,
    )

    print(f"Model written: {tla_p}")
    print(f"Config written: {cfg_p}")
    return tla_p, cfg_p
