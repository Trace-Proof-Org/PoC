# GUIDE Index

Entry point for the TraceProof spec-generation POC. Start with [SKILL.md](SKILL.md), then [00-overview.md](00-overview.md).

Pipeline:

```
Input (code/docs) → Setup & Config → Indexing & Knowledge Cache → Model Generation → Verify & Export
```

This phase stops at a verified, exported TLA+/PlusCal model. Trace Mapper / Conformance Checker / Ticket Agent are out of scope.

---

## Phases

| Phase | File | Output | Done when |
|---|---|---|---|
| 0 — Overview | [00-overview.md](00-overview.md) | Design rationale (MD cache vs RAG, tla-rs vs TLC shell, `.traceproof-poc/` layout) | Architecture and directory layout understood before building |
| 1 — Setup & Config | [01-setup-and-config.md](01-setup-and-config.md) | `run-config.md` | Config exists under `--out` and matches the template; resume vs `--fresh` is explicit |
| 2 — Indexing | [02-indexing.md](02-indexing.md) | `knowledge/_index.md`, `knowledge/<module>.md` | Every considered module is listed with hashes; each cache file matches the template |
| 3 — Model Generation | [03-model-generation.md](03-model-generation.md) | `model/base.tla`, `model/base.cfg`, `model/generation-log.md` | Draft is scoped to a scenario/module and traceable to knowledge files; nothing copied to `export/` |
| 4 — Verify & Export | [04-verify-and-export.md](04-verify-and-export.md) | `export/base.tla`, `export/base.cfg`, `export/manifest.md` | tla-rs verification passed in this run, or the self-repair cap was hit with nothing exported |
| MCP contract | [05-mcp-tla-rs.md](05-mcp-tla-rs.md) | Shared by Phase 3 (optional) and Phase 4 (mandatory) | Syntax check before TLC; bounds come from `base.cfg`, not the agent |

---

## Templates

Write every cache/export file in these formats so later phases can parse them without an LLM.

| Template | Written by | Destination |
|---|---|---|
| [run-config.template.md](run-config.template.md) | Phase 1 | `.traceproof-poc/run-config.md` |
| [knowledge-index.template.md](knowledge-index.template.md) | Phase 2 | `.traceproof-poc/knowledge/_index.md` |
| [knowledge-file.template.md](knowledge-file.template.md) | Phase 2 | `.traceproof-poc/knowledge/<module>.md` |
| [tla-model.template.md](tla-model.template.md) | Phase 3 | Header/comments on `model/base.tla` |
| [export-manifest.template.md](export-manifest.template.md) | Phase 4 | `.traceproof-poc/export/manifest.md` |

---

## Output layout

All run state lives under `--out` (default `.traceproof-poc/`). The POC does not mutate the target repo.

```
.traceproof-poc/
├── run-config.md
├── knowledge/
│   ├── _index.md
│   └── <module>.md
├── model/
│   ├── base.tla
│   ├── base.cfg
│   └── generation-log.md
└── export/
    ├── base.tla
    ├── base.cfg
    └── manifest.md
```

---

## Section map

### [SKILL.md](SKILL.md)

- What this POC does
- Ground rules (local-only, Markdown cache, verify before export, scope, cheap-model-first)
- Suggested build order (Phase 1 → 4)

### [00-overview.md](00-overview.md)

- Scope (first half of TraceProof only)
- Why a Markdown knowledge cache instead of RAG
- Why tla-rs (MCP) instead of shelling out to TLC
- Directory layout
- Model/provider flexibility

### [01-setup-and-config.md](01-setup-and-config.md)

- CLI shape (`traceproof-poc run` flags)
- Suggesting options (source scope, provider/model pairs, candidate scenarios)
- API/provider options (Anthropic default; credentials from env, never cached)
- `run-config.md` contents
- Resume vs `--fresh`

### [02-indexing.md](02-indexing.md)

- Reconnaissance (structural tools before LLM)
- Doc/code cross-check → `## Conflicts`
- Extraction (summary, state, initial condition, invariants, scenario behavior)
- Write cache files + `_index.md`
- Incremental re-indexing via source hashes

### [03-model-generation.md](03-model-generation.md)

- Scope to `--scenario` or one module
- Draft with citations back to `knowledge/*.md`
- Local lint, then write `base.tla` / `base.cfg`
- `generation-log.md`
- Skip regeneration when knowledge hashes are unchanged

### [04-verify-and-export.md](04-verify-and-export.md)

- Syntax validation via tla-rs
- Capped self-repair (default 3; from `run-config.md`)
- Model-check; counterexamples of the *system* are results, not auto-fixes
- Export only after a passing verification in this run
- Handoff contract: `export/base.tla` + `export/manifest.md`

### [05-mcp-tla-rs.md](05-mcp-tla-rs.md)

- Tools used: syntax/parse validation, TLC model checking
- Calling pattern (validate first, pass whole draft, structured errors, enforce cap locally)
- Bounds are human-owned (`model/base.cfg`)

---

## Ground rules (from SKILL)

1. Local-only, terminal-first — no web server, no database.
2. Markdown cache, not RAG.
3. Every model must be verified before export.
4. Stop at verified export; do not implement Trace Mapper / Conformance Checker / Ticket Agent.
5. Cheap model for classification/formatting; strong model for TLA+ drafting.
