# Phase 2 — Indexing & Knowledge Cache

## Goal

Walk the source/docs given in `run-config.md`, extract the facts a
TLA+ model needs, and write them as small, per-module Markdown files under
`knowledge/`. This is the "narrow before you send it to the AI" step —
nothing in this phase should send an entire codebase to an LLM at once.

## Steps

1. **Reconnaissance (cheap-model or plain code analysis).** Map the source
   tree: modules, entry points, obvious concurrency/state boundaries. Prefer
   structural tools (AST parse, grep for lock/mutex/atomic patterns, doc
   headers) over asking an LLM to read everything. Only hand an LLM the
   *specific* files/functions that reconnaissance flags as relevant to the
   chosen scenario (or, if no scenario yet, to each candidate module).

2. **Doc/code cross-check.** If both docs and code are provided for a
   module, extract from both and flag disagreements explicitly rather than
   silently preferring one — this becomes a `## Conflicts` section in that
   module's cache file (see template).

3. **Extraction (strong or cheap model per team's cost tiering — simple
   summarization can use the cheap model; anything judging correctness of a
   concurrency pattern should use the strong model).** For each module,
   produce: a plain-language description of what it does, its relevant
   state variables, an initial-condition sketch, candidate invariants, and
   any scenario-relevant behavior (e.g., "what happens if X crashes after Y
   but before Z").

4. **Write the cache file.** One `.md` file per module/topic under
   `knowledge/`, following `references/templates/knowledge-file.template.md`.
   Update `knowledge/_index.md` with the module name, cache file path, and a
   content hash of the source files it was derived from.

## Incremental re-indexing ("only re-check what changed")

Before extracting a module, compute a hash of its source files (and doc
files, if any) and compare against the hash recorded in `knowledge/_index.md`
from the previous run. If unchanged, skip extraction entirely and reuse the
existing cache file — this is the compiler-style caching the team called out
as the biggest cost saver, applied at indexing time (Phase 3 does its own
caching of generated models per approved rule).

```
if hash(module_sources) == index[module].hash:
    skip  # reuse knowledge/<module>.md as-is
else:
    re-extract and overwrite knowledge/<module>.md, update index hash
```

## Done when

- `knowledge/_index.md` lists every module considered, with hashes.
- Every listed module has a corresponding `knowledge/<module>.md` matching
  the template, including a `## Conflicts` section (even if empty) and a
  `## Open Questions` section for anything the extraction step wasn't
  confident about (surface these to the user rather than guessing).
