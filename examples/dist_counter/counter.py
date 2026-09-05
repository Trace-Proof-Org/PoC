"""Distributed counter — simple example target for TraceProof.

This is a deliberately simplified distributed counter where multiple
nodes can increment a shared counter without proper coordination.
There is an intentional bug: two nodes can both read the same value,
increment it locally, and write back — classic lost-update race.
"""
import threading
import time

counter = 0          # shared state (single integer)
lock_held_by = None  # simulates a buggy non-reentrant advisory lock


def read_counter(node_id: int) -> int:
    """Read current counter value (no synchronization)."""
    return counter


def increment(node_id: int) -> None:
    """
    Buggy increment: read–compute–write without holding a lock.
    Two concurrent callers can both read the same value, then both
    write value+1, losing one increment.
    """
    global counter
    val = read_counter(node_id)
    # Simulate network / processing delay
    time.sleep(0.001)
    counter = val + 1  # BUG: non-atomic — another node may have written meanwhile


def safe_increment(node_id: int) -> None:
    """Correct increment using a lock."""
    global counter, lock_held_by
    lock_held_by = node_id
    val = counter
    counter = val + 1
    lock_held_by = None


# ── Demo ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    N = 3
    increments_per_node = 5

    threads = [
        threading.Thread(target=lambda i=i: [increment(i) for _ in range(increments_per_node)])
        for i in range(N)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    expected = N * increments_per_node
    print(f"counter={counter}, expected={expected}, lost={expected - counter}")
