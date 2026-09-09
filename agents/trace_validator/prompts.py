"""Prompt templates for Trace Validator Agent: trace mapping and model conformance self-repair."""
from __future__ import annotations

import json
from typing import Any


def build_trace_mapping_prompt(
    target_code: str,
    tla_spec: str,
    observed_events: list[dict[str, Any]],
) -> str:
    """
    Builds the prompt instructing the LLM to map physical Python runtime events
    into a TLA+ replay_scenario script.
    """
    events_json = json.dumps(observed_events, indent=2)

    return f"""You are the Trace Validator Agent in the TraceProof formal verification system.
Your job is to translate observed runtime execution events from a Python system into a TLA+ `replay_scenario` script.

The replay scenario will be checked against the formal TLA+ model using tla-mcp's `replay_scenario` tool.
Every step in the scenario must be a valid TLA+ transition predicate matching the action definitions and variables in the spec.

Format Rules:
1. Each transition must start with `step: <TLA+ expression>`.
   - Primed variables (e.g. `owner'`, `pc'`) describe the candidate next state.
   - Unprimed variables (e.g. `owner`, `pc`) describe the current state.
   - Model values in TLA+ constants (e.g., w1, w2) should NOT be enclosed in quotes if they are declared in CONSTANTS Workers.
2. Example step lines:
   step: owner' = w1 /\\ pc'[w1] = "InCS"
   step: pc'[w1] = "Done"
3. Output ONLY the raw scenario step lines. Do NOT use markdown code blocks (no ```). Do NOT include commentary.

Target Python Code:
-------------------
{target_code}

TLA+ Specification:
-------------------
{tla_spec}

Observed Runtime Events:
------------------------
{events_json}

Generate the replay_scenario steps:
"""


def build_conformance_repair_prompt(
    tla_spec: str,
    scenario_text: str,
    failure_info: dict[str, Any],
) -> str:
    """
    Builds the prompt to repair a TLA+ specification when trace validation rejects
    an observed runtime execution step.
    """
    failure_json = json.dumps(failure_info, indent=2)

    return f"""You are the Formal Verification Repair Agent in TraceProof.
The following TLA+ specification failed trace validation because it refused to admit a real execution trace observed from the target system.

Model-Code Conformance Gap:
----------------------------
{failure_json}

Replay Scenario:
----------------
{scenario_text}

Current TLA+ Specification:
---------------------------
{tla_spec}

Instructions:
1. Diagnose why the candidate transition at step {failure_info.get("step_index", 0)} was not enabled.
2. Compare the available actions with the required transition condition.
3. Repair the action definition or state variables in the TLA+ specification so that it accurately models the real code execution.
4. Preserve the invariant definitions (e.g. MutualExclusion). Do NOT remove invariants.
5. Output ONLY the complete, corrected TLA+ module text starting with `---- MODULE base ----` and ending with `====`. Do not use markdown backticks.
"""
