"""Prompt templates for TLA+ specification drafting, linting, and syntax repair."""

from __future__ import annotations


def build_draft_prompt(
    selected: list[tuple[str, str]],
    scenario: str | None,
) -> str:
    """Builds the main prompt for the LLM to draft a TLA+/PlusCal spec."""
    knowledge_block = ""
    for mod, text in selected:
        knowledge_block += f"\n\n--- knowledge/{mod}.md ---\n{text[:3000]}"

    scenario_line = (
        f'Scenario: "{scenario}"' if scenario
        else f'Module-scoped run (no scenario): model the primary module "{selected[0][0]}".'
    )

    primary_module = selected[0][0] if selected else "base"

    return f"""You are a TLA+/PlusCal spec assistant. Draft a TLA+ specification for model checking.

{scenario_line}

Knowledge cache:
{knowledge_block}

Requirements:
1. Use PlusCal algorithm syntax inside a TLA+ MODULE named "base".
2. Include Init and Next predicates (or PlusCal translates them).
3. Define at least one INVARIANT for each candidate invariant in the knowledge files.
4. Add inline comments citing the knowledge file for every non-trivial guard, e.g.:
   \\* Source: knowledge/{primary_module}.md, "<invariant text>"
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


def build_local_lint_fix_prompt(
    tla_text: str,
    errors: list[str],
) -> str:
    """Builds prompt to fix simple regex lint errors using a cheap model."""
    error_list = "\n".join(f"- {e}" for e in errors)
    return f"""The following TLA+ spec has lint errors. Fix them and return ONLY the corrected TLA+ text.

Errors:
{error_list}

Spec:
{tla_text}
"""


def build_syntax_repair_prompt(
    tla_text: str,
    errors: str,
) -> str:
    """Builds prompt to fix syntax/parsing errors reported by tla-mcp."""
    return f"""Fix the following TLA+ specification so it passes syntax validation.
Apply ONLY the minimal targeted fix for the reported errors, do not rewrite the spec.
Return ONLY the corrected TLA+ text, no explanation, no markdown fences.

Errors from tla-rs:
{errors}

Current spec:
{tla_text}
"""
