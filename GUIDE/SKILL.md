---
name: traceproof-spec-poc
description: Build and run the TraceProof proof-of-concept CLI pipeline that indexes a local codebase/docs, caches the extracted knowledge as Markdown, generates a TLA+/PlusCal model from that knowledge, verifies its syntax via the tla-rs MCP server, and exports the verified model. Use this skill whenever the user asks to build, extend, debug, or run the TraceProof POC, the "spec generation POC", the indexing/model-generation pipeline, or any of its four stages (setup, indexing, model generation, verification/export). Also use it when the user references files under a `.traceproof-poc/` output directory, `knowledge/*.md` cache files, or asks to wire up the tla-rs MCP server for syntax checking. This skill covers ONLY the indexing → modeling → verification → export phase; the later Trace Mapper / Conformance Checker / Ticket Agent phase is out of scope and owned separately.
---

# TraceProof Spec-Generation POC

Full file map: [INDEX.md](INDEX.md).

## What this POC does

A terminal (CLI) pipeline, run locally, that takes a path to source code and/or
docs and produces a **verified TLA+/PlusCal model** plus a Markdown knowledge
cache an engineer can read and review. It is intentionally the *first half*
of the TraceProof pipeline described in the project proposal — Spec Assistant
territory, stopping right before Trace Mapper / Conformance Checker / Ticket
Agent, which is a teammate's phase and is explicitly **out of scope** here.

```
Input           Phase 1          Phase 2            Phase 3              Phase 4
(code/docs) ──> Setup & ──────> Indexing & ───────> Model ─────────────> Verify &
                Config          Knowledge Cache      Generation           Export
                    │                │                   │                   │
              run-config.md   knowledge/*.md        model/base.tla     export/*.tla
                                                     model/MC.tla       export/manifest.md
```

Read `references/00-overview.md` first for the full architecture and design
rationale (why MD-cache instead of RAG, why tla-rs, directory layout). Then
follow the phase guide that matches what the user needs:

| Phase | Guide | Covers |
|---|---|---|
| 1 — Setup & Config | `references/01-setup-and-config.md` | CLI args, source discovery, LLM provider/model selection, run-config.md |
| 2 — Indexing | `references/02-indexing.md` | Walking code/docs, chunking, extracting knowledge, writing `knowledge/*.md` |
| 3 — Model Generation | `references/03-model-generation.md` | Turning cached knowledge into `base.tla`/`base.cfg` via PlusCal |
| 4 — Verify & Export | `references/04-verify-and-export.md` | Calling the tla-rs MCP server, fixing syntax errors, exporting the final model |

`references/05-mcp-tla-rs.md` documents the tla-rs MCP tool contract in one
place since both Phase 3 (optional pre-check) and Phase 4 (mandatory
verification) call it.

`references/templates/` holds the exact Markdown templates every cache and
export file must follow — always write files in these formats so a later
phase (or a teammate) can parse them predictably.

## Ground rules for this POC

1. **Local-only, terminal-first.** No web server, no database. All state is
   files on disk under a single output directory (default `.traceproof-poc/`
   in the target repo, overridable with `--out`).
2. **Markdown cache, not RAG.** Per the team's own conclusion, knowledge is
   cached as small, precise, human-readable `.md` files keyed by topic/module
   — not embedded into a vector store. See `references/00-overview.md` for why.
3. **Every model must be verified before export.** Never write a file into
   `export/` that hasn't passed a tla-rs syntax check in this run. If
   verification fails and self-repair (capped, see Phase 3 guide) can't fix
   it, stop and hand the partial model + error back to the human — do not
   guess past the cap.
4. **Scope discipline.** This POC stops at "verified, exported model." Do not
   implement trace mapping, conformance checking, or ticket generation here
   even if it seems like a natural next step — that's explicitly the
   teammate's phase.
5. **Cheap-model-first where it's safe.** Classification/formatting-style
   sub-steps (e.g., file-type routing, simple summarization) can use a
   smaller/cheaper model; drafting the actual TLA+ logic should use the
   strongest configured model. See `references/01-setup-and-config.md` for
   how the POC exposes model choice.

## Suggested build order

If the user is building this from scratch, implement and test phases in
order — each phase's output is the next phase's input, and each is
independently testable from the CLI:

1. Phase 1 (setup/config) — get a working CLI skeleton that parses args and
   writes `run-config.md`.
2. Phase 2 (indexing) — point it at a small real repo, confirm
   `knowledge/*.md` files are readable and accurate.
3. Phase 3 (model generation) — generate a first `base.tla` from the cache,
   even before verification works, to sanity-check prompting.
4. Phase 4 (verify/export) — wire up tla-rs, close the self-repair loop,
   produce a real `export/` output.

Don't try to build all four phases in one pass — verify each phase's file
outputs against its template before moving to the next.
