"""Repair loop: lint the generated TLA+ with TLC's syntax checker and ask the LLM to fix it."""
from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from shared import config
from shared.llm_client import LLMClient
from shared.schemas import LintError, LintResult, SpecDraft, SpecStatus


def _run_tlc_parse(
    tla_text: str,
    module_name: str,
    extra_files: dict[str, str] | None = None,
) -> list[LintError]:
    """Write TLA+ to a temp file and run SANY (TLA+ parser) to collect errors.

    SANY is the canonical TLA+ parser bundled in tla2tools.jar.
    It reports parse/semantic errors and exits 0 on success, non-zero on error.

    Args:
        tla_text: TLA+ source to lint.
        module_name: The module name (also the filename stem).
        extra_files: Extra {name: content} files to write into the temp dir
                     so that EXTENDS dependencies can be resolved.
    """
    errors: list[LintError] = []

    with tempfile.TemporaryDirectory() as tmpdir:
        spec_path = Path(tmpdir) / f"{module_name}.tla"
        spec_path.write_text(tla_text)
        for name, content in (extra_files or {}).items():
            (Path(tmpdir) / name).write_text(content)

        try:
            result = subprocess.run(
                [
                    "java",
                    f"-Xmx{config.TLC_MEMORY_MB}m",
                    "-cp", config.TLC_JAR,
                    "tla2sany.SANY",
                    spec_path.name,   # relative name — SANY resolves from cwd
                ],
                cwd=tmpdir,          # run from the temp dir so EXTENDS can be found
                capture_output=True,
                text=True,
                timeout=60,
            )
            output = result.stdout + result.stderr
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            errors.append(LintError(line=0, message=str(exc)))
            return errors

    # SANY exits 0 on success; any "Errors:" block or exception = failure.
    # Typical forms:
    #   line 42, col 5 to line 42, col 10 of module Foo
    #   Lexical error at line 7, column 3.  Encountered ...
    #   *** Errors: 1
    for line in output.splitlines():
        m = re.search(r"line\s+(\d+)", line, re.IGNORECASE)
        lineno = int(m.group(1)) if m else 0
        lower = line.lower()
        if any(kw in lower for kw in ("error", "exception", "unexpected", "lexical")):
            errors.append(LintError(line=lineno, message=line.strip()))

    return errors


def _repair_prompt(tla_text: str, errors: list[LintError]) -> str:
    error_block = "\n".join(str(e) for e in errors)
    return (
        "The TLA+ specification below has syntax errors reported by TLC. "
        "Fix ONLY the errors listed — do not change the logic or invariants. "
        "Return the corrected TLA+ module as plain text (no markdown fences).\n\n"
        f"## Errors\n{error_block}\n\n"
        f"## Spec\n{tla_text}"
    )


def _fix_system_prompt() -> str:
    return (
        "You are a TLA+ expert. You receive a TLA+ module and a list of parse errors. "
        "Return only the corrected TLA+ module text — no explanation, no fences."
    )


def lint_and_repair(
    draft: SpecDraft,
    llm: Optional[LLMClient] = None,
    max_iterations: int = config.REPAIR_MAX_ITERATIONS,
) -> LintResult:
    """Iterate: parse → if errors → ask LLM to fix → repeat.

    Repairs base_tla first; if that succeeds, repairs mc_tla.
    Returns a LintResult; also mutates draft.base_tla / draft.mc_tla in place.
    """
    if llm is None:
        llm = LLMClient()

    # We lint base_tla + mc_tla sequentially, treating them as a unit.
    targets = [
        ("base_tla", draft.module_name),
        ("mc_tla", "MC"),
    ]

    total_iterations = 0
    all_errors: list[LintError] = []

    for attr, mod_name in targets:
        tla_text: str = getattr(draft, attr)
        if not tla_text.strip():
            continue

        # When linting MC.tla, provide the base spec so SANY can resolve EXTENDS.
        extra: dict[str, str] | None = None
        if attr == "mc_tla" and draft.base_tla.strip():
            extra = {f"{draft.module_name}.tla": draft.base_tla}

        for i in range(max_iterations):
            total_iterations += 1
            errors = _run_tlc_parse(tla_text, mod_name, extra_files=extra)
            if not errors:
                break
            all_errors = errors
            # Ask the LLM to repair
            fixed = llm.complete(
                system=_fix_system_prompt(),
                user=_repair_prompt(tla_text, errors),
            )
            # Strip any accidental fences
            fixed = re.sub(r"^```[^\n]*\n", "", fixed.strip())
            fixed = re.sub(r"\n```$", "", fixed)
            tla_text = fixed
        else:
            # Exhausted iterations
            setattr(draft, attr, tla_text)
            draft.status = SpecStatus.FAILED
            return LintResult(
                success=False,
                errors=errors,
                iterations_used=total_iterations,
                final_tla=tla_text,
            )

        setattr(draft, attr, tla_text)

    draft.status = SpecStatus.LINTED
    return LintResult(
        success=True,
        errors=[],
        iterations_used=total_iterations,
        final_tla=draft.base_tla,
    )
