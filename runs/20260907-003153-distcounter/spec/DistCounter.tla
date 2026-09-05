---- MODULE DistCounter ----
EXTENDS Integers, Sequences, FiniteSets, TLC

CONSTANTS
  Nodes,
  MaxOps

VARIABLES
  counter,
  pc,
  local_val,
  ops_done,
  total_completed

vars == <<counter, pc, local_val, ops_done, total_completed>>

TypeOK ==
  /\ counter \in Nat
  /\ pc \in [Nodes -> {"idle", "read", "done"}]
  /\ local_val \in [Nodes -> Nat]
  /\ ops_done \in [Nodes -> 0..MaxOps]
  /\ total_completed \in 0..(Cardinality(Nodes) * MaxOps)

Init ==
  /\ counter = 0
  /\ pc = [n \in Nodes |-> IF MaxOps > 0 THEN "idle" ELSE "done"]
  /\ local_val = [n \in Nodes |-> 0]
  /\ ops_done = [n \in Nodes |-> 0]
  /\ total_completed = 0

ReadCounter(n) ==
  /\ pc[n] = "idle"
  /\ ops_done[n] < MaxOps
  /\ local_val' = [local_val EXCEPT ![n] = counter]
  /\ pc' = [pc EXCEPT ![n] = "read"]
  /\ UNCHANGED <<counter, ops_done, total_completed>>

WriteCounter(n) ==
  /\ pc[n] = "read"
  /\ counter' = local_val[n] + 1
  /\ ops_done' = [ops_done EXCEPT ![n] = ops_done[n] + 1]
  /\ total_completed' = total_completed + 1
  /\ pc' = [pc EXCEPT ![n] = IF ops_done[n] + 1 < MaxOps THEN "idle" ELSE "done"]
  /\ UNCHANGED <<local_val>>

Next ==
  \E n \in Nodes :
    \/ ReadCounter(n)
    \/ WriteCounter(n)

Spec == Init /\ [][Next]_vars

AllDone == \A n \in Nodes : pc[n] = "done"

NoLostUpdates ==
  AllDone => (counter = Cardinality(Nodes) * MaxOps)

ConsistentCounter ==
  counter = total_completed

=============================================================================