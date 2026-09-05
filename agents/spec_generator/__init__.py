"""Spec generator agent: LLM → TLA+ base spec + MC wrapper + cfg."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from shared.llm_client import LLMClient
from shared.schemas import SpecDraft, SpecStatus


_SYSTEM_PROMPT_PATH = Path(__file__).parent / "prompts" / "system.md"
_SYSTEM_PROMPT = _SYSTEM_PROMPT_PATH.read_text() if _SYSTEM_PROMPT_PATH.is_file() else ""


def _build_user_prompt(source_code: str, module_name: str, description: str) -> str:
    return f"""## Target system
{description}

## Module name to use
{module_name}

## Source code
```
{source_code}
```

Produce the JSON with base_tla, mc_tla, mc_cfg, and notes."""


def _extract_json(text: str) -> dict:
    """Pull the first complete JSON object out of an LLM response.

    Uses bracket counting so nested objects inside field values are handled
    correctly, regardless of whether the model wraps the response in markdown
    fences or returns it bare.
    """
    # Strip a leading ```json ... ``` fence if present
    fenced = re.match(r"^```(?:json)?\s*", text.lstrip())
    if fenced:
        text = text.lstrip()[fenced.end():]
        close_fence = text.rfind("```")
        if close_fence >= 0:
            text = text[:close_fence]

    # Find the first '{' and walk forward counting depth
    start = text.find("{")
    if start < 0:
        raise ValueError("No JSON object found in LLM response")

    depth = 0
    in_string = False
    escape_next = False
    for i, ch in enumerate(text[start:], start):
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])

    raise ValueError("No JSON object found in LLM response")


def generate_spec(
    source_code: str,
    module_name: str,
    description: str = "",
    llm: Optional[LLMClient] = None,
) -> SpecDraft:
    """Call the LLM to generate a TLA+ spec for the given source code.

    Args:
        source_code: The implementation source (any language).
        module_name: The TLA+ module name / file stem to use.
        description: Free-form description of what the system does.
        llm: Optional pre-configured LLMClient; a default is created if None.

    Returns:
        SpecDraft populated with base_tla, mc_tla, mc_cfg.
    """
    if llm is None:
        llm = LLMClient()

    user_prompt = _build_user_prompt(source_code, module_name, description)
    raw = llm.complete(system=_SYSTEM_PROMPT, user=user_prompt, json_mode=True)

    try:
        data = _extract_json(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        return SpecDraft(
            module_name=module_name,
            source_ref=description,
            notes=f"JSON extraction failed: {exc}\n\nRaw response:\n{raw}",
            status=SpecStatus.FAILED,
        )

    return SpecDraft(
        module_name=data.get("module_name", module_name),
        base_tla=data.get("base_tla", ""),
        mc_tla=data.get("mc_tla", ""),
        mc_cfg=data.get("mc_cfg", ""),
        notes=data.get("notes", ""),
        source_ref=description,
        status=SpecStatus.DRAFT,
    )
