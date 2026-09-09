"""Runtime tracer: instruments target system to capture real execution traces."""
from __future__ import annotations

import importlib.util
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class TraceEvent:
    step: int
    worker: str
    action: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecutionTrace:
    target_name: str
    events: list[TraceEvent] = field(default_factory=list)

    def to_scenario(self) -> str:
        """Convert observed execution events into a TLA+ replay_scenario string."""
        lines = []
        for ev in self.events:
            w = ev.worker
            if ev.action == "Acquire":
                lines.append(f"step: owner' = {w}")
            elif ev.action == "ExitCS":
                lines.append(f"step: pc'[{w}] = \"Done\"")
        return "\n".join(lines) + "\n"


def _resolve_target_file(source_path: Path) -> Path:
    if source_path.is_file():
        return source_path
    py_files = [p for p in source_path.glob("*.py") if p.name not in ("__init__.py",)]
    if py_files:
        # Prefer file matching directory name or lock.py
        for p in py_files:
            if p.stem in (source_path.name, "lock", "main"):
                return p
        return py_files[0]
    raise FileNotFoundError(f"No python target found in {source_path}")


def collect_execution_trace(
    source_path: Path,
    num_nodes: int = 2,
) -> ExecutionTrace:
    """
    Instruments and executes the target system under observation
    to capture physical execution events.
    """
    target_file = _resolve_target_file(source_path)
    module_name = target_file.stem

    # Dynamically import module from file
    spec = importlib.util.spec_from_file_location(module_name, target_file)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module from {target_file}")
    target = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(target)

    events: list[TraceEvent] = []
    lock = threading.Lock()
    step_counter = 0

    # Case 1: Target has acquire / release (e.g. distributed lock)
    if hasattr(target, "acquire") and hasattr(target, "release"):
        orig_acquire = target.acquire
        orig_release = target.release

        worker_map = {
            "Worker-1": "w1",
            "Worker-2": "w2",
            "1": "w1",
            "2": "w2",
            "w1": "w1",
            "w2": "w2",
        }

        def traced_acquire(worker_id: str, lease_duration: float = 1.0) -> bool:
            nonlocal step_counter
            res = orig_acquire(worker_id, lease_duration)
            if res:
                with lock:
                    step_counter += 1
                    tla_w = worker_map.get(str(worker_id), f"w{step_counter}")
                    events.append(TraceEvent(
                        step=step_counter,
                        worker=tla_w,
                        action="Acquire",
                        details={"worker_id": worker_id},
                    ))
            return res

        def traced_release(worker_id: str) -> None:
            nonlocal step_counter
            orig_release(worker_id)
            with lock:
                step_counter += 1
                tla_w = worker_map.get(str(worker_id), f"w{step_counter}")
                events.append(TraceEvent(
                    step=step_counter,
                    worker=tla_w,
                    action="ExitCS",
                    details={"worker_id": worker_id},
                ))

        target.acquire = traced_acquire
        target.release = traced_release

        try:
            # Execute sequential non-violating scenario
            if hasattr(target, "do_work"):
                target.do_work("Worker-1", pause_duration=0.01)
                target.do_work("Worker-2", pause_duration=0.01)
            else:
                target.acquire("w1")
                target.release("w1")
                target.acquire("w2")
                target.release("w2")
        finally:
            target.acquire = orig_acquire
            target.release = orig_release

        return ExecutionTrace(target_name=module_name, events=events)

    # Fallback default empty trace
    return ExecutionTrace(target_name=module_name, events=events)
