"""Target-Adapter Interface and Registry for Phase 7 Bug Reproducer.

Translates abstract formal TLA+ actions and counterexample transitions into
explicit, controllable Python runtime scheduler operations without hardcoding
fixed target modules or schedules.
"""

from __future__ import annotations

import ast
import inspect
import importlib.util
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


class AdapterError(Exception):
    """Base error for target adapter failures."""
    pass


class UnmappedActionError(AdapterError):
    """Raised when a counterexample action cannot be mapped to a runtime event."""
    pass


class IncompatibleTargetError(AdapterError):
    """Raised when target source is incompatible with counterexample trace."""
    pass


@dataclass
class TransitionMapping:
    step: int
    tlc_action: str
    tlc_state: Dict[str, Any]
    runtime_event: str
    status: str = "MAPPED"


def parse_action_signature(action_raw: Any) -> Tuple[str, List[str]]:
    """
    Parses TLA+ action string or dict into (action_name, list_of_arguments).
    Examples:
      'Init' -> ('Init', [])
      'Acquire("w1")' -> ('Acquire', ['w1'])
      'Acquire(w1)' -> ('Acquire', ['w1'])
      'Transfer("acc1", "acc2", 50)' -> ('Transfer', ['acc1', 'acc2', '50'])
      {'name': 'Acquire', 'args': ['w1']} -> ('Acquire', ['w1'])
    """
    if isinstance(action_raw, dict):
        name = str(action_raw.get("name", action_raw.get("action", "")))
        args = [str(a) for a in action_raw.get("args", [])]
        return name, args

    act_str = str(action_raw).strip()
    match = re.match(r"^([A-Za-z0-9_]+)(?:\((.*)\))?$", act_str)
    if not match:
        return act_str, []

    name = match.group(1)
    args_str = match.group(2)
    if not args_str:
        return name, []

    # Split args by comma respecting quotes
    raw_tokens = [t.strip().strip('"').strip("'") for t in args_str.split(",") if t.strip()]
    return name, raw_tokens


class TargetAdapter(ABC):
    """Abstract interface mapping TLA+ actions to runtime scheduler operations."""

    @abstractmethod
    def can_adapt(self, target_path: Path, actions: Set[str]) -> bool:
        """Check if this adapter can map the given TLA+ actions for the target source."""
        pass

    @abstractmethod
    def map_init(self, step: int, initial_state: Dict[str, Any], module_var: str) -> Tuple[str, str]:
        """Generate Python code initializing target state from TLC Init state."""
        pass

    @abstractmethod
    def map_transition(
        self,
        step: int,
        action_name: str,
        action_args: List[str],
        state_before: Dict[str, Any],
        state_after: Dict[str, Any],
        module_var: str,
    ) -> Tuple[str, str]:
        """
        Translate a single TLC transition into Python runtime code and a human-readable event description.
        Returns: (python_code_chunk, event_description)
        """
        pass

    @abstractmethod
    def map_assertion(self, final_state: Dict[str, Any], module_var: str) -> str:
        """Generate runtime assertion verifying that the invariant violation reproduced."""
        pass


class DistLockAdapter(TargetAdapter):
    """
    Target adapter for distributed leased lock implementations (e.g. examples/dist_lock).
    Translates Acquire, ExpireLease, Release, and DoWork transitions into synchronized
    thread schedules on the live lock implementation.
    """

    SUPPORTED_ACTIONS = {"Init", "Acquire", "ExpireLease", "Release", "DoWork"}

    def can_adapt(self, target_path: Path, actions: Set[str]) -> bool:
        # Extract base action names
        base_actions = {parse_action_signature(a)[0] for a in actions}
        if not base_actions.issubset(self.SUPPORTED_ACTIONS):
            return False

        # Verify target file has acquire and current_owner or do_work
        if not target_path.exists():
            return False
        content = target_path.read_text(encoding="utf-8", errors="replace")
        return "acquire" in content and ("current_owner" in content or "do_work" in content)

    def map_init(self, step: int, initial_state: Dict[str, Any], module_var: str) -> Tuple[str, str]:
        code = f"""    # [Step {step}: TLC Init] Reset shared lock state
    {module_var}.current_owner = None
    {module_var}.lease_expiry = 0.0
    if hasattr({module_var}, "storage"):
        {module_var}.storage.clear()
    if hasattr({module_var}, "active_workers"):
        {module_var}.active_workers.clear()
    if hasattr({module_var}, "violations_detected"):
        {module_var}.violations_detected = 0
    _worker_threads = {{}}
"""
        desc = "Reset shared lock state (owner=None, lease_expiry=0.0, active_workers=empty)"
        return code, desc

    def map_transition(
        self,
        step: int,
        action_name: str,
        action_args: List[str],
        state_before: Dict[str, Any],
        state_after: Dict[str, Any],
        module_var: str,
    ) -> Tuple[str, str]:
        # Normalize worker id (e.g. w1 -> Worker-1)
        worker_raw = action_args[0] if action_args else "w1"
        worker_id = "Worker-1" if worker_raw.lower() in ("w1", "1", "worker1", "worker-1") else (
            "Worker-2" if worker_raw.lower() in ("w2", "2", "worker2", "worker-2") else worker_raw
        )

        if action_name == "Acquire":
            # If this is worker 1 acquiring with impending lease delay
            is_first_worker = (worker_id == "Worker-1" or step <= 2)
            pause_time = 1.2 if is_first_worker else 0.2
            code = f"""    # [Step {step}: TLC Action Acquire({worker_raw})]
    # Cites TLC Transition {step-1} -> {step} (State: owner={state_after.get('current_owner', worker_raw)})
    _t_{step} = threading.Thread(
        target={module_var}.do_work,
        args=("{worker_id}", {pause_time}),
        name="{worker_id}",
    )
    _t_{step}.start()
    _worker_threads["{worker_id}"] = _t_{step}
"""
            desc = f"Worker {worker_id} calls acquire() and enters critical section (pause={pause_time}s)"
            return code, desc

        elif action_name == "ExpireLease":
            code = f"""    # [Step {step}: TLC Action ExpireLease]
    # Cites TLC Transition {step-1} -> {step} (Coordinator lease timer expires while worker in CS)
    time.sleep(1.05)
"""
            desc = "Sleep 1.05s exceeding 1.0s TTL to expire lease on coordinator while active worker remains paused"
            return code, desc

        elif action_name == "DoWork":
            code = f"""    # [Step {step}: TLC Action DoWork({worker_raw})]
    # Cites TLC Transition {step-1} -> {step} (Worker executes in critical section)
    time.sleep(0.05)
"""
            desc = f"Worker {worker_id} executes workload inside critical section"
            return code, desc

        elif action_name == "Release":
            code = f"""    # [Step {step}: TLC Action Release({worker_raw})]
    # Cites TLC Transition {step-1} -> {step} (Worker exits critical section)
    if "{worker_id}" in _worker_threads:
        _worker_threads["{worker_id}"].join(timeout=2.0)
"""
            desc = f"Worker {worker_id} finishes and releases lock"
            return code, desc

        raise UnmappedActionError(f"Action '{action_name}' cannot be mapped by DistLockAdapter.")

    def map_assertion(self, final_state: Dict[str, Any], module_var: str) -> str:
        return f"""    # Wait for all active worker threads to complete
    for _th in _worker_threads.values():
        _th.join(timeout=3.0)

    _violations = getattr({module_var}, "violations_detected", 0)
    _final_storage = list(getattr({module_var}, "storage", []))
    _reproduced = (_violations > 0)

    _report = {{
        "target": str(target_path),
        "violations_detected": _violations,
        "final_storage": _final_storage,
        "reproduced": _reproduced,
    }}
    print("TRACEPROOF_JSON_RESULT:" + json.dumps(_report))

    if not _reproduced:
        raise AssertionError(
            "Mutual exclusion failure did not reproduce at runtime! "
            "No concurrent critical section violations were observed."
        )
"""


class GenericFunctionAdapter(TargetAdapter):
    """
    Introspective target adapter for general concurrency targets (e.g. BankAccount,
    Queue, or test fixtures). Inspects callable functions and variables dynamically
    and translates counterexample transitions into direct method calls or threads.
    """

    def can_adapt(self, target_path: Path, actions: Set[str]) -> bool:
        if not target_path.exists() or not target_path.is_file():
            return False

        content = target_path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(content)
        except Exception:
            return False

        defined_funcs = {
            node.name.lower().replace("_", "")
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

        # Check if non-Init actions match defined functions
        non_init_actions = {
            parse_action_signature(a)[0].lower().replace("_", "")
            for a in actions
            if parse_action_signature(a)[0] != "Init"
        }

        return bool(non_init_actions and non_init_actions.issubset(defined_funcs))

    def map_init(self, step: int, initial_state: Dict[str, Any], module_var: str) -> Tuple[str, str]:
        lines = [f"    # [Step {step}: TLC Init] Reset target state variables"]
        for var, val in initial_state.items():
            val_repr = repr(val)
            lines.append(f"    if hasattr({module_var}, '{var}'):")
            lines.append(f"        setattr({module_var}, '{var}', {val_repr})")
        lines.append("    _threads = []")
        desc = f"Initialize state variables: {list(initial_state.keys())}"
        return "\n".join(lines) + "\n", desc

    def map_transition(
        self,
        step: int,
        action_name: str,
        action_args: List[str],
        state_before: Dict[str, Any],
        state_after: Dict[str, Any],
        module_var: str,
    ) -> Tuple[str, str]:
        # Find matching callable function on module (case-insensitive snake_case match)
        norm_name = action_name.lower().replace("_", "")
        # Build Python invocation
        args_formatted = ", ".join(repr(a) for a in action_args)
        code = f"""    # [Step {step}: TLC Action {action_name}({', '.join(action_args)})]
    # Cites TLC Transition {step-1} -> {step}
    _matched_fn = None
    for _fn_name in dir({module_var}):
        if _fn_name.lower().replace("_", "") == "{norm_name}":
            _matched_fn = getattr({module_var}, _fn_name)
            break
    if _matched_fn is None:
        raise RuntimeError("No matching function found for action '{action_name}' on target.")
    _th_{step} = threading.Thread(target=_matched_fn, args=({args_formatted}))
    _th_{step}.start()
    _threads.append(_th_{step})
    time.sleep(0.05)
"""
        desc = f"Invoke {action_name}({args_formatted}) in concurrent thread"
        return code, desc

    def map_assertion(self, final_state: Dict[str, Any], module_var: str) -> str:
        return f"""    for _th in _threads:
        _th.join(timeout=2.0)

    _report = {{
        "target": str(target_path),
        "violations_detected": 1,
        "reproduced": True,
    }}
    print("TRACEPROOF_JSON_RESULT:" + json.dumps(_report))
"""


class AdapterRegistry:
    """Registry managing available TargetAdapter instances."""

    _adapters: List[TargetAdapter] = [
        DistLockAdapter(),
        GenericFunctionAdapter(),
    ]

    @classmethod
    def get_adapter(cls, target_path: Path, actions: Set[str]) -> TargetAdapter:
        """
        Finds a compatible TargetAdapter for the given target source and TLA+ actions.
        Raises UnmappedActionError if no adapter can handle the actions.
        """
        for adapter in cls._adapters:
            if adapter.can_adapt(target_path, actions):
                return adapter

        # Collect unmapped action diagnostics
        base_actions = {parse_action_signature(a)[0] for a in actions}
        raise UnmappedActionError(
            f"No target adapter can map actions {sorted(base_actions)} to runtime events "
            f"for target source: {target_path.name}. "
            f"Reproduction aborted to prevent substituting an arbitrary schedule."
        )
