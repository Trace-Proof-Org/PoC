"""Prompt templates and default fallback payloads for the Adversarial Critic Agent."""

from __future__ import annotations
from typing import Any, Dict


def build_adversary_prompt(
    source_name: str,
    source_code: str,
    tla_spec: str,
    counterexample: str,
) -> str:
    """Builds the adversarial prompt to stress-test TLC counterexamples."""
    return f"""You are the TraceProof Adversarial Refinement Agent (Critic).
Your job is to TRY TO BREAK THIS VERIFICATION and find reasons why this model checking counterexample might be a FALSE ALARM (spurious counterexample / specification bug).

Target System Source Code ({source_name}):
```python
{source_code}
```

Formal Specification (TLA+):
```tla
{tla_spec}
```

Discovered TLC Counterexample Trace:
```json
{counterexample}
```

Critically analyze the following 3 adversarial attack vectors:
1. INVARIANT SOUNDNESS: Is the violated invariant (e.g. NoLostUpdates) a true requirement of the system, or is it overly strict / asking for something the implementation never promised?
2. MODEL FIDELITY: Did the TLA+ model omit synchronization, locks, or checks that actually exist in the code? Does the code prevent this interleaving in a way the model missed?
3. INTERLEAVING FEASIBILITY: Can this exact sequence of events physically occur under normal OS / thread scheduling at runtime?

Return ONLY a valid JSON object with these exact keys:
{{
  "verdict": "CONFIRMED_BUG_CANDIDATE" | "MODEL_REPAIR_NEEDED" | "INVARIANT_REFINEMENT_NEEDED",
  "confidence": 0.95,
  "summary": "Clear executive summary of your adversarial judgment",
  "invariant_critique": "Assessment of whether the invariant is valid or flawed",
  "model_fidelity_critique": "Assessment of whether the model faithfully reflects code behavior",
  "concurrency_critique": "Assessment of whether the thread race is physically possible",
  "code_citations": ["file:line or function references in source code"],
  "reproduction_guidance": "Concrete guidance for the Bug Confirmation Agent on how to reproduce this interleaving in Python"
}}"""


FALLBACK_CRITIQUE: Dict[str, Any] = {
    "verdict": "CONFIRMED_BUG_CANDIDATE",
    "confidence": 0.90,
    "summary": "The model faithfully captures the non-atomic read-modify-write in increment(). No synchronization is present in the active code path.",
    "invariant_critique": "NoLostUpdates is a fundamental correctness property for a shared counter. It correctly expects final counter to reflect all completed increments.",
    "model_fidelity_critique": "The model accurately abstracts read_counter and the delayed counter assignment as separate atomic steps, which matches the un-synchronized implementation.",
    "concurrency_critique": "The lost-update interleaving (Read -> Read -> Write -> Write) is a classic race condition and readily achievable with standard preemptive OS thread scheduling.",
    "code_citations": ["counter.py:20 (increment)", "counter.py:30 (counter = val + 1)"],
    "reproduction_guidance": "Spawn two threads calling increment(). Introduce a small artificial pause between read_counter and counter assignment to deterministically trigger lost update."
}
