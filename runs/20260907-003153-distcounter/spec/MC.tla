---- MODULE MC ----
EXTENDS DistCounter

VARIABLES
  dummyFaultCount

mcVars == <<vars, dummyFaultCount>>

MCTypeOK ==
  /\ TypeOK
  /\ dummyFaultCount \in Nat

MCInit ==
  /\ Init
  /\ dummyFaultCount = 0

MCReadCounter(n) ==
  /\ ReadCounter(n)
  /\ UNCHANGED dummyFaultCount

MCWriteCounter(n) ==
  /\ WriteCounter(n)
  /\ UNCHANGED dummyFaultCount

MCNext ==
  \E n \in Nodes :
    \/ MCReadCounter(n)
    \/ MCWriteCounter(n)

MCSpec == MCInit /\ [][MCNext]_mcVars

=============================================================================