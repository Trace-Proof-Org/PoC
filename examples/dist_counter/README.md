# TraceProof example: Distributed Counter

A simple distributed counter where `N` nodes share a single integer.
The `increment()` function is **intentionally buggy**: it performs a
non-atomic read–modify–write, so concurrent increments can lose updates.

## What TraceProof should find

TLC should find an invariant violation showing that two nodes can both
read the same value and both write back `value + 1`, producing a final
counter that is less than expected.

## Run it

```bash
python -m harness.pipeline \
    --module DistCounter \
    --desc "Distributed counter with non-atomic increment bug" \
    examples/dist_counter/counter.py
```
