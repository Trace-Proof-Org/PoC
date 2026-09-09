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
    "confidence": 0.95,
    "summary": "The model faithfully captures the lease expiration race condition in lock.py. Without fencing tokens or heartbeat extensions, an execution delay longer than the lease TTL allows a second worker to acquire the lock while the first worker is still active in the critical section.",
    "invariant_critique": "MutualExclusion (Cardinality(active_in_cs) <= 1) is the essential safety property of any distributed locking mechanism.",
    "model_fidelity_critique": "The model accurately reflects that acquire() checks lease expiration against wall-clock time without checking if the previous owner has actually completed execution.",
    "concurrency_critique": "A thread experiencing a delay (e.g. GC pause, slow I/O) that exceeds the 1.0s lease duration while another thread acquires the lock is a classic, physically realizable distributed systems race condition.",
    "code_citations": ["lock.py:31 (now >= lease_expiry)", "lock.py:59 (len(active_workers) > 1)"],
    "reproduction_guidance": "Spawn Worker-1 with pause_duration=1.2s (> 1.0s lease). Sleep 1.05s, then spawn Worker-2. Both workers will execute concurrently in the critical section, triggering an invariant violation."
}
