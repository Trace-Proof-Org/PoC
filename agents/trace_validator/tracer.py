"""Runtime tracer: instruments target system to capture real execution traces."""
from __future__ import annotations

import importlib
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class TraceEvent:
    step: int
    node: int
    action: str
    counter: int
    pc: str


@dataclass
class ExecutionTrace:
    target_name: str
    events: list[TraceEvent] = field(default_factory=list)

    def to_scenario(self) -> str:
        """Convert observed trace into a TLA+ replay_scenario string."""
        lines = []
        for ev in self.events:
            node = ev.node
            if ev.action == "Read":
                lines.append(f'step: pc\'[{node}] = "Write"')
            elif ev.action == "Write":
                lines.append(f'step: pc\'[{node}] = "Done"')
        return "\n".join(lines) + "\n"


def collect_counter_trace(source_path: Path, num_nodes: int = 2) -> ExecutionTrace:
    """Run counter.py under tracing and capture the interleaved event stream."""
    src_dir = source_path if source_path.is_dir() else source_path.parent
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))

    # Import target dynamically
    module_name = source_path.stem if source_path.is_file() else "counter"
    target = importlib.import_module(module_name)
    target.counter = 0

    events: list[TraceEvent] = []
    lock = threading.Lock()
    step_counter = 0

    orig_read = target.read_counter

    def traced_read(node_id: int) -> int:
        nonlocal step_counter
        val = orig_read(node_id)
        with lock:
            step_counter += 1
            events.append(TraceEvent(
                step=step_counter,
                node=node_id + 1,  # TLA+ 1-indexed process
                action="Read",
                counter=val,
                pc="Write",
            ))
        return val

    target.read_counter = traced_read

    def worker(node_id: int):
        nonlocal step_counter
        # 1. Read
        val = target.read_counter(node_id)
        time.sleep(0.002)
        # 2. Write
        with lock:
            step_counter += 1
            target.counter = val + 1
            events.append(TraceEvent(
                step=step_counter,
                node=node_id + 1,
                action="Write",
                counter=target.counter,
                pc="Done",
            ))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_nodes)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Restore original read
    target.read_counter = orig_read

    return ExecutionTrace(target_name="dist_counter", events=events)
