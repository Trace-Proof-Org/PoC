"""Distributed Lock with Lease Expiration (Leased Lock).

Target system for TraceProof formal verification.
Demonstrates the classic lease expiration race condition (Redlock anomaly).

In distributed systems, locks have leases (Time-To-Live / TTL) to prevent
permanent deadlocks if a client crashes. However, if a worker node experiences
an unexpected delay (garbage collection pause, network latency, or slow disk I/O)
longer than the lease duration, the coordinator expires the lease and grants the lock
to another worker — leading to simultaneous execution in the critical section!
"""

import threading
import time
from typing import Optional, Set, List

# Shared lock manager state
current_owner: Optional[str] = None
lease_expiry: float = 0.0

# Shared protected resource state
storage: List[str] = []
active_workers: Set[str] = set()
violations_detected: int = 0


def acquire(worker_id: str, lease_duration: float = 1.0) -> bool:
    """Acquire the lock if it is unheld or if the current lease has expired."""
    global current_owner, lease_expiry
    now = time.time()
    if current_owner is None or now >= lease_expiry:
        current_owner = worker_id
        lease_expiry = now + lease_duration
        return True
    return False


def release(worker_id: str) -> None:
    """Release the lock if currently held by the caller."""
    global current_owner
    if current_owner == worker_id:
        current_owner = None


def do_work(worker_id: str, pause_duration: float = 0.0) -> bool:
    """
    Worker acquires the lock, executes in critical section, then releases.
    BUG: If pause_duration exceeds lease_duration, the lease expires while
    this worker is inside the critical section, allowing another worker to enter!
    """
    global violations_detected
    if not acquire(worker_id, lease_duration=1.0):
        return False

    try:
        active_workers.add(worker_id)

        # Invariant check: at most 1 worker in critical section simultaneously
        if len(active_workers) > 1:
            violations_detected += 1
            print(f"  [VIOLATION] Mutual exclusion broken! Active workers in CS: {active_workers}")

        # Simulate task duration, slow I/O, or GC pause
        if pause_duration > 0:
            time.sleep(pause_duration)

        storage.append(f"{worker_id}-write")
        return True
    finally:
        active_workers.discard(worker_id)
        release(worker_id)


# ── Demo ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=== 1. Sequential Execution (Expected Safe) ===")
    do_work("Worker-1", pause_duration=0.1)
    do_work("Worker-2", pause_duration=0.1)
    print(f"Storage: {storage}, Violations: {violations_detected}")

    print("\n=== 2. Concurrent Execution with Lease Expiry (Bug Demonstration) ===")
    storage.clear()
    violations_detected = 0

    # Worker 1 pauses for 1.2s (> 1.0s lease duration)
    t1 = threading.Thread(target=do_work, args=("Worker-1", 1.2), name="Worker-1")
    t1.start()

    # Wait 1.05s so Worker 1's lease expires on the coordinator
    time.sleep(1.05)

    # Worker 2 acquires the lock because Worker 1's lease expired, but Worker 1 is still inside!
    t2 = threading.Thread(target=do_work, args=("Worker-2", 0.2), name="Worker-2")
    t2.start()

    t1.join()
    t2.join()

    print(f"Final Storage: {storage}")
    print(f"Total Mutual Exclusion Violations Detected: {violations_detected}")
