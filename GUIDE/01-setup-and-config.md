# Phase 1 — Setup & Config

## Goal

Take the user's local input path(s), a few high-level choices, and produce
`run-config.md` — the single source of truth every later phase reads instead
of re-asking the user anything.

## CLI shape

Keep it a plain terminal command, no interactive server:

```
traceproof-poc run \
  --source ./path/to/repo \
  --docs   ./path/to/docs           # optional, can repeat --source/--docs
  --out    .traceproof-poc          # optional, default shown
  --provider anthropic              # or: openai, local, etc.
  --model-strong  claude-opus-5     # used for spec drafting (Phase 3)
  --model-cheap   claude-haiku-4-5  # used for classification/formatting (Phase 2)
  --scenario "payment succeeds but order service crashes"   # optional, scopes Phase 3
```

If `--source`/`--docs` point at nothing readable, fail fast with a clear
message — don't silently produce an empty knowledge cache.

## Suggesting options to the user

When the user runs the tool without full flags (or asks "what are my
options"), the CLI should offer sane suggestions rather than demanding a
fully-specified command up front:

- **Source type detection**: if `--source` looks like a git repo, offer to
  scope to a subdirectory/module rather than the whole tree (ties into
  "don't feed the AI the whole codebase").
- **Provider/model options**: list the configured providers and their
  strong/cheap model pairs; let the user accept a default pairing or pick
  their own per role (drafting vs. classification).
- **Scenario suggestion**: if no `--scenario` is given, Phase 2's indexing
  pass can surface 2-3 candidate scenarios (e.g., crash windows, missing
  guards it noticed) for the user to pick from before Phase 3 runs — keeps
  the state space check scoped to something concrete rather than exhaustive.

## API/provider options

Support at minimum:

- **Anthropic API** (direct) — recommended default for the strong model.
- **A cheap/small model option** — can be a smaller model from the same
  provider, or a local/open-weight model if the user has one available.
- Provider credentials are read from environment variables
  (`ANTHROPIC_API_KEY`, etc.), never written into `run-config.md` or any
  other cache file.

## `run-config.md` — what it must contain

Use the template at `references/templates/run-config.template.md`. At
minimum, record: source paths, output directory, chosen scenario (if any),
provider + model choice per role, and a timestamp. Every later phase reads
this file first instead of taking flags directly, so Phase 2–4 stay
reproducible from `run-config.md` alone.

## Done when

- `run-config.md` exists under `--out` and matches the template.
- Re-running with `--out` pointing at an existing run either resumes
  (see incremental notes in `02-indexing.md`) or errors clearly if
  `--fresh` wasn't passed — don't silently overwrite a previous run's cache.
