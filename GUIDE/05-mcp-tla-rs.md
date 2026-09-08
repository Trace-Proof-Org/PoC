# tla-rs MCP Server — Tool Contract

tla-rs is the MCP server that exposes the TLA+/TLC toolbox to agents in this
POC. It's used from two places:

- **Phase 3** (optional, local pre-check before a full round-trip)
- **Phase 4** (mandatory — nothing is exported without passing here)

Because it exposes the full TLA+ API, agents have complete control of the
TLC toolbox through it rather than shelling out to `tlc`/`sany` directly.
That means: prefer the MCP tool calls below over invoking any TLA+ binaries
directly from this POC's own code, so results stay structured and the same
contract works regardless of how tla-rs is deployed for the user.

> This POC only needs a subset of what tla-rs can do. Confirm the exact tool
> names/params against the running server (`tools/list` over MCP) before
> wiring up calls — the table below is the expected shape based on the
> tla-rs feature set, not a guarantee of exact naming.

## Tools this POC uses

| Purpose | When | Expected input | Expected output |
|---|---|---|---|
| Syntax / parse validation | Phase 3 (optional), Phase 4 step 2 (mandatory) | path or inline text of `base.tla` | pass/fail + list of syntax errors with line numbers |
| Model checking (TLC run) | Phase 4 step 4 | `base.tla` + `base.cfg` (bounds, invariants to check) | pass/fail, and if fail: a counterexample trace |

## Calling pattern

1. Always validate (syntax) before model-checking — don't spend a TLC run on
   a spec that won't even parse.
2. Pass the *whole* current draft each call — tla-rs is stateless per call
   from this POC's point of view; don't assume it remembers a previous
   attempt.
3. On failure, capture the structured error (not just stdout text) and
   attach it verbatim to the self-repair prompt described in
   `04-verify-and-export.md` — the more precise the error handed back to the
   drafting model, the fewer repair iterations it should take.
4. Respect the self-repair cap from `run-config.md`; tla-rs itself has no
   concept of that cap, so the POC's own loop must enforce it.

## Bounds for model checking

The engineer sets bounds (set sizes, symmetry reduction), not the agent —
consistent with the rest of the TraceProof proposal's stance that bound
selection is a separate, human-owned problem. Read bounds from
`model/base.cfg`, which Phase 3 should have written with sane, small
defaults for a POC-scale scenario. Don't have the agent infer or widen
bounds on its own during Phase 4.
