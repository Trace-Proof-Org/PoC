# Distributed Leased Lock (Target System)

This target demonstrates the **Lease Expiration Race Condition** (commonly known in distributed systems as the *Redlock Anomaly*).

---

## 1. System Overview

In distributed architectures (e.g., Redis, ZooKeeper, AWS DynamoDB, Kubernetes), locks are granted with a **lease / Time-To-Live (TTL)**:
- **Why leases exist**: If a worker node crashes while holding a lock, the lock must eventually expire automatically to prevent permanent deadlocks.
- **How it works**: When a worker acquires a lock, the coordinator sets `lease_expiry = now + lease_duration`. Once `now >= lease_expiry`, the coordinator considers the lock free.

---

## 2. The Invariant: Mutual Exclusion

$$\text{MutualExclusion} \triangleq \text{Cardinality}(\text{active\_workers}) \le 1$$

At any point in time, at most one worker is permitted to execute inside the protected critical section.

---

## 3. The Vulnerability

A worker node is not notified when its lease expires. If the worker encounters an unexpected delay (e.g., a Garbage Collection pause, network latency spike, or slow disk I/O) that lasts longer than `lease_duration`:

1. **Worker 1** acquires lock (lease = 1.0s).
2. **Worker 1** enters the critical section, but pauses for 1.2s.
3. At $t = 1.0\text{s}$, the coordinator expires Worker 1's lease.
4. **Worker 2** requests the lock at $t = 1.05\text{s}$. The coordinator grants it!
5. **Worker 2** enters the critical section while Worker 1 is still inside.
6. **Result**: Both Worker 1 and Worker 2 execute in the critical section concurrently, violating **Mutual Exclusion** and corrupting shared storage.
