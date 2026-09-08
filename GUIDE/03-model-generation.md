# Phase 3 — Model Generation

## Goal

Turn the cached knowledge for the chosen scenario/module into a draft
PlusCal/TLA+ model: `model/base.tla` (+ `base.cfg`), plus a
`generation-log.md` recording what was drafted and why.

## Scope the model, don't boil the ocean

Per the team's "don't test the whole system at once" decision, the model
should be scoped to the chosen `--scenario` (from `run-config.md`) or, if
none was chosen, to a single module at a time — never an attempt to model
the entire indexed codebase in one spec. If the scenario touches multiple
modules, pull only the relevant `knowledge/*.md` files for those modules,
not the full `knowledge/` directory.

## Steps

1. **Load inputs.** Read `run-config.md` for the scenario/scope, and only
   the `knowledge/<module>.md` files relevant to it.
2. **Draft the model (strong model).** Produce an initial condition, a
   next-state relation, and candidate invariants, directly traceable back to
   the cached knowledge — every non-trivial condition in the draft should be
   attributable to a specific fact in a `knowledge/*.md` file (cite the
   source module inline as a comment, similar in spirit to Specula's
   file:line annotations, but pointing at the knowledge cache file instead
   of raw source).
3. **Local lint (cheap check, no MCP needed yet).** Basic sanity checks
   (unbalanced constructs, obviously unbound variables) before spending an
   MCP round-trip on it.
4. **Write `model/base.tla` and `model/base.cfg`.** Follow
   `references/templates/tla-model.template.md` for the accompanying
   documentation block/comments expected at the top of the file.
5. **Log it.** Append a dated entry to `model/generation-log.md`: which
   scenario, which knowledge files were used, and a one-line summary of the
   drafted invariants.

## Handoff to Phase 4

Phase 3's output is not yet trusted — it has not been checked by tla-rs.
Do not copy anything from `model/` into `export/` from this phase; that
only happens after Phase 4's verification passes (or the self-repair loop
in Phase 4 fixes it within its cap).

## Regeneration / caching note

If a scenario has already been modeled in a previous run and its underlying
`knowledge/*.md` files' hashes haven't changed (see Phase 2), skip
regeneration and reuse the existing `model/base.tla` — consistent with the
team's "reuse results instead of regenerating them" decision.
