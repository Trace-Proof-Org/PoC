"""
Phase 2 — Indexing & Knowledge Cache
Walks source/docs from run-config.md, extracts per-module facts,
writes knowledge/<module>.md + knowledge/_index.md.

No LLM required: structural recon (AST + grep) is used as the fallback.
When an API key is available the LLM fills in the knowledge files.
"""

from __future__ import annotations

import ast
import hashlib
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__",
    ".mypy_cache", ".pytest_cache", "dist", "build", ".eggs",
}
CONCURRENCY_PATTERNS = re.compile(
    r"\b(lock|mutex|semaphore|atomic|thread|asyncio|await|async def|"
    r"concurrent|subprocess|multiprocess|queue|channel|barrier|condition)\b",
    re.IGNORECASE,
)
CRASH_PATTERNS = re.compile(
    r"\b(crash|assert|raise|exception|fatal|panic|abort|timeout|retry|"
    r"rollback|compensat|idempoten)\b",
    re.IGNORECASE,
)
CODE_EXTENSIONS = {".py", ".go", ".ts", ".js", ".java", ".rs", ".c", ".cpp", ".h"}
DOC_EXTENSIONS  = {".md", ".txt", ".rst"}

INDEX_FILENAME = "knowledge/_index.md"
RUN_CONFIG     = "run-config.md"


# ---------------------------------------------------------------------------
# run-config.md reader (simple line-by-line, no external parser)
# ---------------------------------------------------------------------------

class RunConfig(NamedTuple):
    source_paths: list[Path]
    docs_paths:   list[Path]
    output_dir:   Path
    scenario:     str | None   # None when "none — module-scoped run"
    provider:     str
    model_strong: str
    model_cheap:  str


def _parse_run_config(out: Path) -> RunConfig:
    cfg_path = out / RUN_CONFIG
    if not cfg_path.exists():
        raise IndexError(f"No run-config.md under {out}. Run 'traceproof-poc run' first.")

    data: dict[str, str] = {}
    for line in cfg_path.read_text().splitlines():
        m = re.match(r"^-\s+\*\*(.+?)\*\*:\s*(.+)$", line)
        if m:
            data[m.group(1).strip()] = m.group(2).strip()

    def paths(key: str) -> list[Path]:
        val = data.get(key, "none")
        if val == "none":
            return []
        return [Path(p.strip()) for p in val.split(",") if p.strip()]

    scenario_raw = data.get("Scenario", "none — module-scoped run")
    scenario = None if "none" in scenario_raw.lower() else scenario_raw

    return RunConfig(
        source_paths = paths("Source paths"),
        docs_paths   = paths("Docs paths"),
        output_dir   = Path(data.get("Output dir", str(out))),
        scenario     = scenario,
        provider     = data.get("Provider", "local"),
        model_strong = data.get("Model (strong / drafting)", ""),
        model_cheap  = data.get("Model (cheap / classification)", ""),
    )


# ---------------------------------------------------------------------------
# File walking
# ---------------------------------------------------------------------------

def _walk_files(root: Path) -> list[Path]:
    """Recursively yield readable files, skipping noise dirs."""
    files: list[Path] = []
    for p in sorted(root.rglob("*")):
        if any(skip in p.parts for skip in SKIP_DIRS):
            continue
        if p.is_file():
            files.append(p)
    return files


def _group_by_module(
    source_paths: list[Path],
    docs_paths:   list[Path],
    output_dir:   Path,
) -> dict[str, dict[str, list[Path]]]:
    """
    Returns {module_name: {"code": [...], "docs": [...]}}

    Grouping rule (simple):
    - Each immediate subdirectory of a source root → its own module.
    - Files at the root level of a source path → "root" module.
    - Docs are matched by stem to a module; leftover docs → "docs" module.
    """
    modules: dict[str, dict[str, list[Path]]] = {}

    for src in source_paths:
        src = src.resolve()
        out_resolved = output_dir.resolve()
        files = _walk_files(src)
        for f in files:
            # skip the output dir
            try:
                f.relative_to(out_resolved)
                continue
            except ValueError:
                pass
            if f.suffix not in CODE_EXTENSIONS | DOC_EXTENSIONS:
                continue
            # module name = immediate child of src that contains this file
            try:
                rel = f.relative_to(src)
            except ValueError:
                continue
            parts = rel.parts
            mod = parts[0] if len(parts) > 1 else "root"
            # strip extension from single-file "module" names
            if len(parts) == 1:
                mod = f.stem
            bucket = "code" if f.suffix in CODE_EXTENSIONS else "docs"
            modules.setdefault(mod, {"code": [], "docs": []})
            modules[mod][bucket].append(f)

    # attach docs_paths by stem match
    for dp in docs_paths:
        dp = dp.resolve()
        for f in _walk_files(dp):
            if f.suffix not in DOC_EXTENSIONS:
                continue
            stem = f.stem.lower()
            matched = False
            for mod in modules:
                if mod.lower() == stem or stem in mod.lower():
                    modules[mod]["docs"].append(f)
                    matched = True
                    break
            if not matched:
                modules.setdefault("docs", {"code": [], "docs": []})
                modules["docs"]["docs"].append(f)

    return modules


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------

def _hash_files(files: list[Path]) -> str:
    h = hashlib.sha256()
    for f in sorted(files):
        if f.exists():
            h.update(str(f).encode())
            h.update(f.read_bytes())
    return h.hexdigest()[:16]


# ---------------------------------------------------------------------------
# Structural recon (no LLM)
# ---------------------------------------------------------------------------

class ReconResult(NamedTuple):
    summary:     str
    state_vars:  list[str]
    init_sketch: str
    invariants:  list[str]
    scenario_rel: str
    conflicts:   str
    open_qs:     list[str]


def _recon_python(files: list[Path]) -> dict:
    """Extract names, classes, functions via AST."""
    classes, functions, imports = [], [], []
    for f in files:
        if f.suffix != ".py":
            continue
        try:
            tree = ast.parse(f.read_text(errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                classes.append(node.name)
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                functions.append(node.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append(node.module.split(".")[0])
    return {"classes": classes[:20], "functions": functions[:30], "imports": list(set(imports))[:20]}


def _grep_patterns(files: list[Path]) -> dict[str, list[str]]:
    hits: dict[str, list[str]] = {"concurrency": [], "crash": []}
    for f in files:
        try:
            text = f.read_text(errors="replace")
        except Exception:
            continue
        for line in text.splitlines():
            if CONCURRENCY_PATTERNS.search(line):
                snippet = line.strip()[:120]
                if snippet not in hits["concurrency"]:
                    hits["concurrency"].append(snippet)
            if CRASH_PATTERNS.search(line):
                snippet = line.strip()[:120]
                if snippet not in hits["crash"]:
                    hits["crash"].append(snippet)
    return {k: v[:8] for k, v in hits.items()}


def _recon_docs(doc_files: list[Path]) -> list[str]:
    headings = []
    for f in doc_files:
        try:
            for line in f.read_text(errors="replace").splitlines():
                if line.startswith("#"):
                    headings.append(line.strip())
        except Exception:
            pass
    return headings[:20]


def _structural_extract(
    module: str,
    code_files: list[Path],
    doc_files:  list[Path],
    scenario:   str | None,
) -> ReconResult:
    py_info  = _recon_python(code_files)
    patterns = _grep_patterns(code_files + doc_files)
    headings = _recon_docs(doc_files)

    # Summary
    file_names = [f.name for f in code_files[:6]]
    summary = f"Module '{module}' contains {len(code_files)} code file(s): {', '.join(file_names) or 'none'}."
    if py_info["classes"]:
        summary += f" Key classes: {', '.join(py_info['classes'][:5])}."
    if py_info["functions"]:
        summary += f" Notable functions: {', '.join(py_info['functions'][:8])}."
    if headings:
        summary += f" Doc headings: {', '.join(headings[:4])}."

    # State vars (classes + key patterns)
    state_vars = []
    for cls in py_info["classes"][:8]:
        state_vars.append(f"{cls} instance state (fields unknown without LLM)")
    if not state_vars:
        state_vars = ["(state variables not determined — no Python classes found; LLM extraction needed)"]

    # Initial condition
    init_sketch = "Initial state not determined from structural scan alone. LLM extraction needed."

    # Invariants
    invariants = []
    if patterns["concurrency"]:
        invariants.append("Concurrent access to shared state must be properly serialised.")
    if patterns["crash"]:
        invariants.append("System must handle crash/exception paths without data corruption.")
    if not invariants:
        invariants = ["(no invariants inferred structurally — LLM extraction needed)"]

    # Scenario relevance
    if scenario:
        has_crash = bool(patterns["crash"])
        has_conc  = bool(patterns["concurrency"])
        rel = f"Scenario: \"{scenario}\".\n"
        rel += "Concurrency patterns spotted: " + ("yes" if has_conc else "no") + ". "
        rel += "Crash/exception patterns spotted: " + ("yes" if has_crash else "no") + "."
    else:
        parts = []
        if patterns["concurrency"]:
            parts.append(f"concurrency patterns: {patterns['concurrency'][0][:80]}")
        if patterns["crash"]:
            parts.append(f"crash patterns: {patterns['crash'][0][:80]}")
        rel = "; ".join(parts) if parts else "No notable crash windows or race conditions found structurally."

    # Conflicts (doc vs code)
    conflicts = "none" if not (doc_files and code_files) else (
        "Doc files present alongside code — verify consistency manually (LLM extraction needed for precise diff)."
    )

    # Open questions
    open_qs = ["Structural scan only — full knowledge extraction requires an LLM API key."]
    if py_info["imports"]:
        ext_imports = [i for i in py_info["imports"] if i not in {"os","sys","re","ast","hashlib","pathlib","typing","datetime"}]
        if ext_imports:
            open_qs.append(f"External dependencies: {', '.join(ext_imports[:8])} — may carry relevant state or error semantics.")

    return ReconResult(
        summary=summary,
        state_vars=state_vars,
        init_sketch=init_sketch,
        invariants=invariants,
        scenario_rel=rel,
        conflicts=conflicts,
        open_qs=open_qs,
    )


# ---------------------------------------------------------------------------
# LLM extraction (optional — falls back to structural if no key)
# ---------------------------------------------------------------------------

# Credential resolution is centralised in setup_phase.
from setup_phase import (
    credential_present as _credential_present,
    get_api_key as _get_api_key,
    get_base_url as _get_base_url,
)


def _llm_extract(
    module: str,
    code_files: list[Path],
    doc_files:  list[Path],
    scenario:   str | None,
    provider: str,
    model: str,
) -> ReconResult | None:
    """Call LLM to fill the knowledge file. Returns None if unavailable."""
    api_key = _get_api_key(provider)
    if not api_key:
        return None

    # Build a compact code snippet (first 120 lines per file, up to 3 files)
    snippets: list[str] = []
    for f in list(code_files)[:3] + list(doc_files)[:2]:
        try:
            lines = f.read_text(errors="replace").splitlines()[:120]
            snippets.append(f"### {f.name}\n" + "\n".join(lines))
        except Exception:
            pass
    code_block = "\n\n".join(snippets) if snippets else "(no readable files)"

    scenario_line = f'Scenario to focus on: "{scenario}"' if scenario else "No specific scenario — summarise the module generally."

    prompt = f"""You are a TLA+ spec assistant. Analyse this module and return a JSON object.

Module: {module}
{scenario_line}

Source (truncated):
{code_block}

Return ONLY valid JSON with these exact keys:
{{
  "summary": "plain-language description of what this module does",
  "state_vars": ["bullet 1", "bullet 2"],
  "init_sketch": "plain-language initial-condition sketch",
  "invariants": ["invariant 1 in plain language", "invariant 2"],
  "scenario_rel": "how this module relates to the scenario or notable crash windows",
  "conflicts": "doc-vs-code disagreements or none",
  "open_questions": ["question 1", "question 2"]
}}"""

    base_url = _get_base_url(provider)

    try:
        if provider == "anthropic":
            return _call_anthropic(api_key, model, prompt)
        else:
            return _call_openai_compat(api_key, base_url, model, prompt)
    except Exception as e:
        print(f"  warning: LLM call failed for module '{module}': {e}", file=sys.stderr)
        return None


def _parse_llm_json(text: str) -> dict | None:
    import json
    # Strip markdown code fences if present
    text = re.sub(r"^```[a-z]*\n?", "", text.strip(), flags=re.MULTILINE)
    text = re.sub(r"```$", "", text.strip(), flags=re.MULTILINE)
    try:
        return json.loads(text.strip())
    except Exception:
        # Try to find JSON object in the response
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except Exception:
                pass
    return None


def _call_openai_compat(api_key: str, base_url: str, model: str, prompt: str) -> ReconResult | None:
    import urllib.request, json
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 1024,
        "temperature": 0,
    }).encode()
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())
    text = data["choices"][0]["message"]["content"]
    return _json_to_recon(text)


def _call_anthropic(api_key: str, model: str, prompt: str) -> ReconResult | None:
    import urllib.request, json
    payload = json.dumps({
        "model": model,
        "max_tokens": 1024,
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
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())
    text = data["content"][0]["text"]
    return _json_to_recon(text)


def _json_to_recon(text: str) -> ReconResult | None:
    d = _parse_llm_json(text)
    if not d:
        return None
    return ReconResult(
        summary      = str(d.get("summary", "")),
        state_vars   = [str(s) for s in d.get("state_vars", [])],
        init_sketch  = str(d.get("init_sketch", "")),
        invariants   = [str(s) for s in d.get("invariants", [])],
        scenario_rel = str(d.get("scenario_rel", "")),
        conflicts    = str(d.get("conflicts", "none")),
        open_qs      = [str(s) for s in d.get("open_questions", [])],
    )


# ---------------------------------------------------------------------------
# Knowledge file writer
# ---------------------------------------------------------------------------

def _write_knowledge_file(
    out:        Path,
    module:     str,
    code_files: list[Path],
    doc_files:  list[Path],
    src_hash:   str,
    recon:      ReconResult,
) -> Path:
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    sources_str = ", ".join(str(f) for f in code_files) or "none"
    docs_str    = ", ".join(str(f) for f in doc_files)  or "none"

    state_block = "\n".join(f"- {v}" for v in recon.state_vars) or "- (none)"
    inv_block   = "\n".join(f"- {v}" for v in recon.invariants) or "- (none)"
    oq_block    = "\n".join(f"- {q}" for q in recon.open_qs)    or "- (none)"

    content = f"""# Knowledge: {module}

- **Source files**: {sources_str}
- **Doc files**: {docs_str}
- **Source hash**: {src_hash}
- **Last extracted**: {ts}

## Summary
{recon.summary}

## State
{state_block}

## Initial Condition (sketch)
{recon.init_sketch}

## Candidate Invariants
{inv_block}

## Scenario Relevance
{recon.scenario_rel}

## Conflicts
{recon.conflicts}

## Open Questions
{oq_block}
"""
    knowledge_dir = out / "knowledge"
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    path = knowledge_dir / f"{module}.md"
    path.write_text(content)
    return path


def _write_index(out: Path, rows: list[tuple[str, Path, str, str]]) -> Path:
    """rows: [(module, cache_path, hash, timestamp)]. Only writes if hash content changed."""
    lines = [
        "# Knowledge Index\n",
        "One row per module considered during indexing. "
        "`hash` is used to skip re-extraction on later runs when the underlying source/docs haven't changed.\n",
        "| Module | Cache file | Source hash | Last extracted |",
        "|---|---|---|---|",
    ]
    for module, cache_path, src_hash, ts in rows:
        rel = cache_path.relative_to(out)
        lines.append(f"| {module} | {rel} | {src_hash} | {ts} |")
    content = "\n".join(lines) + "\n"
    idx = out / "knowledge" / "_index.md"
    # Only rewrite if hashes/modules changed (ignore timestamps for mtime stability)
    def _hash_only(text: str) -> str:
        """Strip timestamps so we compare only module+hash columns."""
        return "\n".join(
            re.sub(r"\| [\d\-T:+Z]+ \|$", "| <ts> |", line)
            for line in text.splitlines()
        )
    if idx.exists() and _hash_only(idx.read_text()) == _hash_only(content):
        return idx
    idx.write_text(content)
    return idx


def _load_existing_index(out: Path) -> dict[str, str]:
    """Returns {module: hash} from existing _index.md, or {}."""
    idx = out / "knowledge" / "_index.md"
    if not idx.exists():
        return {}
    result = {}
    for line in idx.read_text().splitlines():
        m = re.match(r"^\|\s*(.+?)\s*\|\s*.+?\s*\|\s*([a-f0-9]+)\s*\|", line)
        if m and m.group(1) not in ("Module", "---"):
            result[m.group(1)] = m.group(2)
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class IndexError(Exception):
    pass


def index_run(output_dir: str | Path = ".traceproof-poc") -> Path:
    """
    Phase 2 entry point. Reads run-config.md, walks sources, writes knowledge/.
    Returns the path to knowledge/_index.md.
    """
    out = Path(output_dir).expanduser().resolve()
    cfg = _parse_run_config(out)

    if not cfg.source_paths:
        raise IndexError("No source paths in run-config.md.")

    modules = _group_by_module(cfg.source_paths, cfg.docs_paths, out)
    if not modules:
        raise IndexError("No indexable files found under source paths.")

    existing = _load_existing_index(out)
    use_llm  = _credential_present(cfg.provider)

    rows:     list[tuple[str, Path, str, str]] = []
    skipped:  list[str] = []
    extracted:list[str] = []

    for module, buckets in sorted(modules.items()):
        code_files = buckets["code"]
        doc_files  = buckets["docs"]
        all_files  = code_files + doc_files

        if not all_files:
            continue

        src_hash = _hash_files(all_files)
        ts       = datetime.now(timezone.utc).isoformat(timespec="seconds")

        # Incremental cache check
        if existing.get(module) == src_hash:
            cache_path = out / "knowledge" / f"{module}.md"
            if cache_path.exists():
                skipped.append(module)
                rows.append((module, cache_path, src_hash, ts))
                continue

        # Extract
        recon: ReconResult | None = None
        if use_llm:
            model = cfg.model_cheap or cfg.model_strong
            recon = _llm_extract(module, code_files, doc_files, cfg.scenario,
                                  cfg.provider, model)
        if recon is None:
            recon = _structural_extract(module, code_files, doc_files, cfg.scenario)

        cache_path = _write_knowledge_file(out, module, code_files, doc_files, src_hash, recon)
        extracted.append(module)
        rows.append((module, cache_path, src_hash, ts))

    idx_path = _write_index(out, rows)

    # Report
    if extracted:
        print(f"Extracted: {', '.join(extracted)}")
    if skipped:
        print(f"Skipped (unchanged): {', '.join(skipped)}")
    print(f"Index written: {idx_path}")

    return idx_path
