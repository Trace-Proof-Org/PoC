# Overview & Design Rationale

## Scope

This POC implements the **first half** of TraceProof's agent pipeline:

```
[code/docs] -> index -> cache knowledge (MD) -> generate TLA+ model -> verify (tla-rs MCP) -> export
```

It does **not** implement Trace Mapper, Conformance Checker, or Ticket Agent.
Those consume this POC's `export/` output as their input and are a separate
phase owned by a teammate. Do not build them here, even partially.

## Why a Markdown knowledge cache instead of RAG

The team decided against embedding-based RAG for this stage:

- The knowledge needed per module (control flow, shared-state, invariants,
  doc/code conflicts) is a small, well-scoped set of facts, not a large fuzzy
  corpus — a keyed lookup ("give me module X's cached knowledge") beats
  similarity search.
- Correctness matters more than recall: a missed or fuzzily-retrieved
  invariant produces a silently wrong TLA+ model. Deterministic, complete
  inclusion of a module's cache file avoids that failure mode.
- Human auditability: an engineer (or the sign-off step later in the full
  pipeline) can open a `.md` file and read exactly what the agent believes
  about a module, in plain language, before any model is generated from it.
- It mirrors how a compiler skips unchanged files — see "incremental re-index"
  in `02-indexing.md` — which is much easier to reason about with file-based
  caching than with a vector index.

RAG-style retrieval is still the right tool *inside* a target codebase search
(e.g., "which files are relevant to this module") — but that's a code-search
problem, best solved with normal code-analysis tooling (AST/call-graph, grep,
etc.), not embeddings over the codebase. This POC's indexing phase should
prefer structural code analysis over semantic search for that reason.

## Why tla-rs (MCP) instead of shelling out to TLC directly

tla-rs is used for two distinct purposes in this POC:

1. **Model validation** — sanity-checking that the model generated in Phase 3
   is well-formed before spending cycles on anything else.
2. **Model checking** — running the actual TLC check that Phase 4 requires
   before a model can be exported.

Because tla-rs exposes the full TLA+/TLC API as MCP tools, the agent gets
programmatic, structured results (not scraped terminal output) it can act on
in a self-repair loop. See `05-mcp-tla-rs.md` for the tool contract.

## Directory layout

All state lives under a single output directory (default `.traceproof-poc/`),
so the POC never mutates the target repo it's analyzing:

```
.traceproof-poc/
├── run-config.md            # Phase 1 output — how this run was configured
├── knowledge/
│   ├── _index.md             # Phase 2 — map of module -> cache file, hashes
│   ├── <module-a>.md         # Phase 2 — one cache file per module/topic
│   └── <module-b>.md
├── model/
│   ├── base.tla               # Phase 3 — generated PlusCal/TLA+
│   ├── base.cfg
│   └── generation-log.md      # Phase 3 — prompts/decisions, for audit
└── export/
    ├── base.tla                # Phase 4 — final, verified model (copy)
    ├── base.cfg
    └── manifest.md             # Phase 4 — verification result summary
```

Every `.md` file this POC writes should follow the matching template in
`references/templates/`. Consistent structure is what lets later phases (or
a teammate's tooling) parse these files without an LLM in the loop.

## Model/provider flexibility

Phase 1 lets the user pick an LLM provider/model per the team's cost-tiering
decision (strong model for spec drafting, cheap model for classification and
formatting). Don't hardcode a single provider — see `01-setup-and-config.md`.
