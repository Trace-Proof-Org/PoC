"""
Phase 1 — Setup & Config
Validates sources/docs, writes run-config.md under --out.
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------

PROVIDER_DEFAULTS: dict[str, dict[str, str]] = {
    "anthropic": {
        "strong": "claude-opus-4-5",
        "cheap":  "claude-haiku-4-5",
        "base_url": "https://api.anthropic.com",
    },
    "openai": {
        "strong": "gpt-4o",
        "cheap":  "gpt-4o-mini",
        "base_url": "https://api.openai.com/v1",
    },
    "gemini": {
        "strong": "gemini-2.5-pro",
        "cheap":  "gemini-2.0-flash",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
    },
    "xai": {
        "strong": "grok-3",
        "cheap":  "grok-3-mini",
        "base_url": "https://api.x.ai/v1",
    },
    "openrouter": {
        "strong": "",
        "cheap":  "",
        "base_url": "https://openrouter.ai/api/v1",
    },
    "groq": {
        "strong": "",
        "cheap":  "",
        "base_url": "https://api.groq.com/openai/v1",
    },
    "ollama": {
        "strong": "",
        "cheap":  "",
        "base_url": "http://127.0.0.1:11434/v1",
    },
    "local": {
        "strong": "local",
        "cheap":  "local",
        "base_url": "",
    },
}

RUN_CONFIG_FILENAME = "run-config.md"


class SetupError(Exception):
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _check_paths(paths: list[str], label: str) -> list[Path]:
    resolved = []
    for p in paths:
        path = Path(p).expanduser().resolve()
        if not path.exists():
            raise SetupError(f"{label} path does not exist: {path}")
        if not os.access(path, os.R_OK):
            raise SetupError(f"{label} path is not readable: {path}")
        resolved.append(path)
    return resolved


def _detect_git_root(source: Path) -> Path | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=source if source.is_dir() else source.parent,
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return Path(result.stdout.strip())
    except Exception:
        pass
    return None


def _suggest_subdirs(source: Path) -> list[Path]:
    """Return immediate child dirs that look like code modules."""
    if not source.is_dir():
        return []
    skip = {".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache"}
    return [
        d for d in sorted(source.iterdir())
        if d.is_dir() and d.name not in skip and not d.name.startswith(".")
    ]


def _key_for_provider(provider: str) -> str:
    """Return the primary env-var name for *provider* (for warning messages)."""
    slug = provider.upper().replace("-", "_")
    return f"{slug}_API_KEY"


# ---------------------------------------------------------------------------
# Public credential helpers — import these in every phase module
# ---------------------------------------------------------------------------

def get_api_key(provider: str) -> str:
    """
    Resolve API key for *provider* using the priority chain:

      1. {PROVIDER}_API_KEY   e.g. ANTHROPIC_API_KEY, XAI_API_KEY
      2. TRACEPROOF_API_KEY   generic key that works for any provider
      3. LLM_API_KEY          alternative generic fallback

    Returns the first non-empty value found, or "" if none is set.
    """
    if provider == "local":
        return ""
    slug = provider.upper().replace("-", "_")
    return (
        os.environ.get(f"{slug}_API_KEY", "")
        or os.environ.get("TRACEPROOF_API_KEY", "")
        or os.environ.get("LLM_API_KEY", "")
    )


def get_base_url(provider: str) -> str:
    """
    Resolve base URL for *provider* using the priority chain:

      1. {PROVIDER}_BASE_URL   e.g. XAI_BASE_URL
      2. TRACEPROOF_BASE_URL   generic override for any provider
      3. LLM_BASE_URL / OPENAI_BASE_URL
      4. Built-in default from PROVIDER_DEFAULTS (if registered)
    """
    slug = provider.upper().replace("-", "_")
    return (
        os.environ.get(f"{slug}_BASE_URL", "")
        or os.environ.get("TRACEPROOF_BASE_URL", "")
        or os.environ.get("LLM_BASE_URL", "")
        or os.environ.get("OPENAI_BASE_URL", "")
        or PROVIDER_DEFAULTS.get(provider, {}).get("base_url", "")
    )


def credential_present(provider: str) -> bool:
    """Return True if an API key is available for *provider*."""
    if provider == "local":
        return False
    return bool(get_api_key(provider))


def _credential_present(provider: str) -> bool:
    """Internal alias used by setup_phase warning logic (local → True)."""
    if provider == "local":
        return True
    return credential_present(provider)


def _write_run_config(
    *,
    out: Path,
    source_paths: list[Path],
    docs_paths: list[Path],
    provider: str,
    model_strong: str,
    model_cheap: str,
    scenario: str | None,
    self_repair_cap: int,
    notes: str,
) -> Path:
    sources_str = ", ".join(str(p) for p in source_paths)
    docs_str = ", ".join(str(p) for p in docs_paths) if docs_paths else "none"
    scenario_str = scenario if scenario else "none — module-scoped run"
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")

    content = f"""# Run Config

- **Created**: {ts}
- **Source paths**: {sources_str}
- **Docs paths**: {docs_str}
- **Output dir**: {out}
- **Scenario**: {scenario_str}
- **Provider**: {provider}
- **Model (strong / drafting)**: {model_strong}
- **Model (cheap / classification)**: {model_cheap}
- **Self-repair cap**: {self_repair_cap}

## Notes
{notes if notes else ""}
"""
    config_path = out / RUN_CONFIG_FILENAME
    config_path.write_text(content)
    return config_path


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def setup_run(
    *,
    source_paths: Sequence[str],
    docs_paths: Sequence[str] = (),
    output_dir: str | Path = ".traceproof-poc",
    provider: str = "anthropic",
    model_strong: str = "",
    model_cheap: str = "",
    scenario: str | None = None,
    self_repair_cap: int = 3,
    notes: str = "",
    fresh: bool = False,
) -> Path:
    """Validate paths, write run-config.md, return its path.

    Raises SetupError on invalid input.
    Prints warnings to stdout (never raises) for missing credentials.
    Returns the path to run-config.md.
    """
    if not source_paths:
        raise SetupError("At least one --source path is required.")

    src = _check_paths(list(source_paths), "--source")
    docs = _check_paths(list(docs_paths), "--docs")

    out = Path(output_dir).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)

    config_path = out / RUN_CONFIG_FILENAME
    if config_path.exists() and not fresh:
        print(f"Resuming existing run — {config_path}")
        print("  (pass --fresh to overwrite run-config.md and start fresh)")
        return config_path

    # Resolve model defaults
    defaults = PROVIDER_DEFAULTS.get(provider, {})
    strong = model_strong or defaults.get("strong", "")
    cheap  = model_cheap  or defaults.get("cheap",  "")

    if provider not in PROVIDER_DEFAULTS:
        if not strong or not cheap:
            raise SetupError(
                f"Unknown provider '{provider}': pass --model-strong and --model-cheap explicitly."
            )

    if not _credential_present(provider):
        key_var = _key_for_provider(provider)
        print(
            f"warning: No credentials visible for provider '{provider}'. "
            f"Set {key_var} (or TRACEPROOF_API_KEY) before running index/generate."
        )

    path = _write_run_config(
        out=out,
        source_paths=src,
        docs_paths=docs,
        provider=provider,
        model_strong=strong,
        model_cheap=cheap,
        scenario=scenario,
        self_repair_cap=self_repair_cap,
        notes=notes,
    )
    return path


def suggest_options(source: str | None = None) -> None:
    """Print provider list and (optionally) source-tree hints."""
    print("Registered providers and default model pairs:")
    print()
    for name, info in PROVIDER_DEFAULTS.items():
        strong = info.get("strong") or "<set --model-strong>"
        cheap  = info.get("cheap")  or "<set --model-cheap>"
        print(f"  {name:<14} strong={strong}  cheap={cheap}")

    if source:
        p = Path(source).expanduser().resolve()
        print()
        if not p.exists():
            print(f"  note: --source '{source}' does not exist.")
            return
        git_root = _detect_git_root(p)
        if git_root:
            print(f"  git repo: {git_root}")
            if p != git_root and p.is_dir():
                print(f"  --source is a sub-tree of the repo — good for scoped indexing.")
            elif p == git_root:
                subdirs = _suggest_subdirs(p)
                if subdirs:
                    print("  Consider scoping --source to a subdirectory:")
                    for d in subdirs[:6]:
                        print(f"    {d}")
        else:
            print(f"  '{p}' is not inside a git repo.")

        print()
        print("  Tip: use --scenario to scope Phase 3 to a concrete crash window, e.g.:")
        print('    --scenario "payment succeeds but order service crashes"')
