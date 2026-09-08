# TraceProof PoC — Complete User Manual

This manual provides complete, detailed instructions on how to install, configure, run, test, and troubleshoot the **TraceProof Proof-of-Concept (PoC)**: an agentic pipeline that transforms source code and documentation into a **Markdown knowledge cache**, synthesizes a **formal TLA+/PlusCal specification**, validates and model checks it using the **tla-rs MCP server** (with an automated capped self-repair loop), and exports a verified model.

---

## Table of Contents

1. [Architecture & Pipeline Scope](#1-architecture--pipeline-scope)
2. [Prerequisites & System Requirements](#2-prerequisites--system-requirements)
3. [Installation & Setup](#3-installation--setup)
4. [Environment Variables & Credentials](#4-environment-variables--credentials)
   - [4.1 API Key Resolution Order](#41-api-key-resolution-order)
   - [4.2 Base URL Resolution Order](#42-base-url-resolution-order)
   - [4.3 Model Context Protocol (tla-rs) Configuration](#43-model-context-protocol-tla-rs-configuration)
5. [CLI Reference & Subcommands](#5-cli-reference--subcommands)
   - [Phase 0: Options & Inspection (`options`)](#phase-0-options--inspection-options)
   - [Phase 1: Setup & Configuration (`run`)](#phase-1-setup--configuration-run)
   - [Phase 2: Indexing & Knowledge Cache (`index`)](#phase-2-indexing--knowledge-cache-index)
   - [Phase 3: Model Generation (`generate`)](#phase-3-model-generation-generate)
   - [Phase 4: Syntax Verification, Self-Repair & Export (`verify`)](#phase-4-syntax-verification-self-repair--export-verify)
6. [Step-by-Step Run Scenarios](#6-step-by-step-run-scenarios)
   - [Scenario A: Quickstart / Zero-Credentials Offline Run](#scenario-a-quickstart--zero-credentials-offline-run)
   - [Scenario B: Cloud LLM Run (Anthropic / OpenAI / Gemini / xAI / Groq)](#scenario-b-cloud-llm-run-anthropic--openai--gemini--xai--groq)
   - [Scenario C: Targeted Scenario Run](#scenario-c-targeted-scenario-run)
   - [Scenario D: Resuming vs. Fresh Execution](#scenario-d-resuming-vs-fresh-execution)
7. [Workspace Layout & Artifact Structure](#7-workspace-layout--artifact-structure)
   - [Directory Tree (`.traceproof-poc/`)](#directory-tree-traceproof-poc)
   - [Artifact Specifications](#artifact-specifications)
8. [Programmatic Python API](#8-programmatic-python-api)
9. [Running Automated Tests](#9-running-automated-tests)
10. [Troubleshooting & FAQs](#10-troubleshooting--faqs)

---

## 1. Architecture & Pipeline Scope

TraceProof PoC implements the **first half of the TraceProof pipeline** (Spec Assistant). It operates exclusively on local files and produces formal specifications without modifying your target code.

```text
Target Codebase & Docs
         │
         ▼
 ┌─────────────────┐
 │ Phase 1: Setup  │ ──► .traceproof-poc/run-config.md
 └────────┬────────┘
          ▼
 ┌─────────────────┐
 │ Phase 2: Index  │ ──► .traceproof-poc/knowledge/_index.md
 └────────┬────────┘     .traceproof-poc/knowledge/<module>.md
          ▼
 ┌─────────────────┐
 │Phase 3: Generate│ ──► .traceproof-poc/model/base.tla
 └────────┬────────┘     .traceproof-poc/model/base.cfg
          │              .traceproof-poc/model/generation-log.md
          ▼
 ┌─────────────────┐
 │ Phase 4: Verify │ ──► .traceproof-poc/export/base.tla
 └─────────────────┘     .traceproof-poc/export/base.cfg
                         .traceproof-poc/export/manifest.md
```

### Key Architectural Pillars:
1. **Target Code Safety**: The pipeline **never alters the analyzed codebase**. All state, caches, logs, models, and export artifacts are written to an isolated output directory (`--out`, defaults to `.traceproof-poc/`).
2. **Markdown Knowledge Cache**: Instead of opaque embeddings or vector databases, facts about modules (state variables, control flow, invariants, doc/code conflicts) are stored in transparent Markdown files for human review and auditing.
3. **Compiler-Style Incremental Caching**: Files are indexed and tracked via SHA-256 content hashes. Unchanged modules are skipped in subsequent indexing runs, and unchanged knowledge files prevent redundant model regeneration.
4. **Verification Gate**: No model is ever copied to `export/` without passing syntax validation. System counterexamples discovered by the model checker are captured and reported in `manifest.md` as valid diagnostic output.
5. **Clear Handoff Boundary**: Downstream components (Trace Mapper, Conformance Checker, Ticket Agent) are intentionally separate and consume the output in `export/`.

---

## 2. Prerequisites & System Requirements

- **Python**: Version **3.10** or newer (3.10, 3.11, and 3.12 supported).
- **Operating System**: Linux, macOS, or Windows (WSL recommended).
- **Network / LLM Access**: Optional. An API key for Anthropic, OpenAI, Gemini, Groq, xAI, OpenRouter, or a local Ollama instance can be supplied. If no API key is set, the pipeline automatically runs in **offline structural fallback mode**.
- **tla-rs MCP Server (Optional, for Phase 4)**: A binary implementing the Model Context Protocol stdio interface for TLA+ (`tla-mcp`). For testing and development, built-in fake/stub MCP clients allow testing Phase 4 without an external server installed.

---

## 3. Installation & Setup

### 3.1 Clone and Set Up Virtual Environment

```bash
# Navigate to the repository root
cd /path/to/PoC

# Create a Python virtual environment
python3 -m venv .venv

# Activate the virtual environment
source .venv/bin/activate       # On Windows PowerShell: .venv\Scripts\Activate.ps1
```

### 3.2 Install TraceProof PoC

Install the package in editable mode with development/test dependencies:

```bash
pip install -e ".[dev]"
```

Verify that the CLI command is registered:

```bash
traceproof-poc --help
```

You can also run directly with python:
```bash
python3 cli.py --help
```

---

## 4. Environment Variables & Credentials

TraceProof PoC features **zero third-party SDK dependencies** for LLM communication (using standard Python `urllib` and `json`) and implements a flexible credential priority chain.

### 4.1 API Key Resolution Order

When an LLM call is made, the pipeline searches environment variables in this order:

1. **Provider-Specific Key**:
   - `anthropic` → `ANTHROPIC_API_KEY`
   - `openai` → `OPENAI_API_KEY`
   - `gemini` → `GEMINI_API_KEY`
   - `xai` → `XAI_API_KEY`
   - `groq` → `GROQ_API_KEY`
   - `openrouter` → `OPENROUTER_API_KEY`
2. **Universal TraceProof Key**: `TRACEPROOF_API_KEY` (Works for **any** provider!)
3. **Generic LLM Fallback**: `LLM_API_KEY`

> [!TIP]
> To quickly configure any provider without worrying about provider-specific prefixes, set `TRACEPROOF_API_KEY`:
> ```bash
> export TRACEPROOF_API_KEY="sk-..."
> ```

### 4.2 Base URL Resolution Order

For custom proxies, internal gateways, or local models:

1. `{PROVIDER}_BASE_URL` (e.g., `XAI_BASE_URL`, `OPENROUTER_BASE_URL`, `OLLAMA_BASE_URL`)
2. `TRACEPROOF_BASE_URL` (Universal override)
3. `LLM_BASE_URL` or `OPENAI_BASE_URL`
4. **Built-in Defaults**:
   | Provider | Default Base URL |
   |---|---|
   | `anthropic` | `https://api.anthropic.com` |
   | `openai` | `https://api.openai.com/v1` |
   | `gemini` | `https://generativelanguage.googleapis.com/v1beta/openai` |
   | `xai` | `https://api.x.ai/v1` |
   | `groq` | `https://api.groq.com/openai/v1` |
   | `openrouter` | `https://openrouter.ai/api/v1` |
   | `ollama` | `http://127.0.0.1:11434/v1` |
   | `local` | *(offline, no HTTP calls)* |

### 4.3 Model Context Protocol (tla-rs) Configuration

Phase 4 communicates with `tla-rs` over JSON-RPC stdio. Configure it via:

| Variable | Default | Purpose |
|---|---|---|
| `TLA_RS_MCP_COMMAND` | `tla-mcp` | Path or shell command to launch the tla-rs MCP server binary |
| `TLA_RS_MAX_STATES` | `1000` | Maximum explored state budget before returning `limit_reached` |
| `TLA_RS_MAX_DEPTH` | `30` | Maximum search tree exploration depth |
| `TLA_RS_MAX_SECONDS` | `15` | Wall-clock timeout in seconds for TLC model checking |

Example:
```bash
export TLA_RS_MCP_COMMAND="/usr/local/bin/tla-mcp"
export TLA_RS_MAX_STATES=5000
export TLA_RS_MAX_SECONDS=30
```

---

## 5. CLI Reference & Subcommands

All pipeline commands are invoked via `traceproof-poc <subcommand>` (or `python3 cli.py <subcommand>`).

```text
traceproof-poc [-h] {options,run,index,generate,verify} ...
```

---

### Phase 0: Options & Inspection (`options`)

Inspect registered LLM providers, view default strong/cheap model pairings, and inspect a source repository for module boundaries.

```bash
# Print general provider and model options
traceproof-poc options

# Inspect a codebase directory for module recommendations
traceproof-poc options --source ./my_service
```

#### Output details:
- Lists all pre-configured providers and their default model pairs (e.g. `gpt-4o` + `gpt-4o-mini`, `claude-opus-4-5` + `claude-haiku-4-5`).
- Detects the git repository root.
- Suggests candidate subdirectories to avoid feeding an entire monolithic codebase into an LLM.
- Provides example scenario hints.

---

### Phase 1: Setup & Configuration (`run`)

Validates readability of source and documentation paths, checks provider credential readiness, and writes the frozen configuration to `run-config.md`.

```bash
traceproof-poc run \
  --source ./src \
  --docs ./docs \
  --out .traceproof-poc \
  --provider anthropic \
  --model-strong claude-opus-4-5 \
  --model-cheap claude-haiku-4-5 \
  --scenario "worker crashes while holding distributed lock" \
  --self-repair-cap 3 \
  --notes "Analyzing race condition during worker shutdown"
```

#### CLI Arguments:
- `--source PATH` *(Required, repeatable)*: Path to source directory or file. Can be specified multiple times.
- `--docs PATH` *(Optional, repeatable)*: Path to documentation files or folders.
- `--out DIR` *(Optional, default: `.traceproof-poc`)*: Target directory storing run artifacts.
- `--provider NAME` *(Optional, default: `anthropic`)*: Provider identifier (`anthropic`, `openai`, `gemini`, `xai`, `groq`, `openrouter`, `ollama`, `local`).
- `--model-strong MODEL` *(Optional)*: Model used for TLA+ specification drafting and self-repair. Defaults to provider recommendation.
- `--model-cheap MODEL` *(Optional)*: Economical model used for classification, indexing, and linting. Defaults to provider recommendation.
- `--scenario TEXT` *(Optional)*: Scopes model generation to a specific execution flow or crash window. If omitted, the run is module-scoped.
- `--self-repair-cap N` *(Optional, default: `3`)*: Maximum self-repair iterations allowed during Phase 4 verification.
- `--notes TEXT` *(Optional)*: Freeform engineering notes saved to `run-config.md`.
- `--fresh` *(Optional flag)*: Forces overwriting an existing `run-config.md`. Without this flag, invoking `run` on an existing output directory safely resumes it.

---

### Phase 2: Indexing & Knowledge Cache (`index`)

Reads `run-config.md`, walks the target sources and docs, computes SHA-256 hashes, extracts state variables, candidate invariants, and conflicts, and writes individual Markdown knowledge files.

```bash
traceproof-poc index --out .traceproof-poc
```

#### Execution Details:
1. **Module Discovery**: Groups files by top-level directories under `--source` and `--docs`.
2. **Incremental Cache Check**: Computes a SHA-256 hash of all files belonging to each module. If the hash matches the entry in `knowledge/_index.md`, extraction is skipped.
3. **Reconnaissance & Extraction**:
   - **With API Key**: Calls the cheap model with sampled code/doc snippets to summarize behavior, identify state variables, draft initial conditions, extract candidate invariants, and detect documentation/code conflicts.
   - **Without API Key (or `provider=local`)**: Runs structural AST extraction (identifying Python classes, async/sync methods) and regex pattern matching (searching for concurrency terms like `lock`, `mutex`, `channel`, `atomic`, and crash/recovery terms like `retry`, `exception`, `fatal`).
4. **Outputs**: Writes `knowledge/<module>.md` for each module and updates the catalog in `knowledge/_index.md`.

---

### Phase 3: Model Generation (`generate`)

Synthesizes cached facts from `knowledge/` into a formal specification (`model/base.tla`) and TLC configuration (`model/base.cfg`).

```bash
traceproof-poc generate --out .traceproof-poc
```

#### Execution Details:
1. **Module Selection**:
   - If `--scenario` is set: Scores modules based on scenario keyword density and concurrency patterns, selecting up to the 3 most relevant modules.
   - If no scenario: Selects the module with the highest concentration of concurrency signals.
2. **Incremental Cache Check**: Checks the combined module hashes against the last `cache-key` in `model/generation-log.md`. If unchanged, regeneration is skipped.
3. **Specification Drafting**:
   - **With API Key**: Instructs the strong model to generate a PlusCal/TLA+ specification named `MODULE base`, citing specific knowledge files for non-trivial guards.
   - **Without API Key (or `provider=local`)**: Constructs a valid, syntactically complete fallback TLA+ specification with typed variables, `Init`/`Next` relations, step counters, and invariant definitions derived from candidate invariants.
4. **Local Lint**: Evaluates the spec for basic syntax requirements (`MODULE`, `Init`, `Next`) before writing files.
5. **Outputs**: Writes `model/base.tla`, `model/base.cfg`, and appends an audit record to `model/generation-log.md`. **Never writes to `export/`**.

---

### Phase 4: Syntax Verification, Self-Repair & Export (`verify`)

Communicates with the `tla-rs` MCP server via stdio JSON-RPC to validate syntax and model-check the specification, using an automated self-repair loop on syntax failures.

```bash
traceproof-poc verify --out .traceproof-poc
```

#### Execution Details:
1. **Connect to MCP Server**: Spawns the command configured in `TLA_RS_MCP_COMMAND` (default: `tla-mcp`) and performs the MCP JSON-RPC handshake.
2. **Syntax Validation Gate**: Invokes the `validate_spec` tool. If syntax errors occur:
   - Feeds the error log to the strong LLM for targeted repair.
   - Updates `model/base.tla` and re-validates.
   - Repeats up to `--self-repair-cap` times.
   - If validation fails and no LLM credentials are set or the repair cap is exhausted, execution aborts; **nothing is written to `export/`**.
3. **Model Checking**:
   - Invokes `check_spec` with bounds from `model/base.cfg` and environment limits (`TLA_RS_MAX_STATES`, `TLA_RS_MAX_DEPTH`, `TLA_RS_MAX_SECONDS`).
   - If a spec-internal error occurs, triggers repair.
   - If `limit_reached` occurs, prompts the user to increase state bounds.
   - If a system `counterexample` is discovered, it is treated as a valid diagnostic finding.
4. **Final Export**:
   - Copies `model/base.tla` → `export/base.tla`
   - Copies `model/base.cfg` → `export/base.cfg`
   - Generates `export/manifest.md` recording verification results, counterexample traces (if any), knowledge files used, and repair statistics.

---

## 6. Step-by-Step Run Scenarios

### Scenario A: Quickstart / Zero-Credentials Offline Run

You can run the entire pipeline immediately without network connectivity or API tokens:

```bash
# 1. Initialize run configuration using the local provider
traceproof-poc run \
  --source ./tests \
  --out .traceproof-poc \
  --provider local \
  --fresh

# 2. Extract facts using structural AST and regex analysis
traceproof-poc index --out .traceproof-poc

# 3. Generate a structurally sound TLA+ specification
traceproof-poc generate --out .traceproof-poc
```

Check the generated specification:
```bash
cat .traceproof-poc/model/base.tla
```

---

### Scenario B: Cloud LLM Run (Anthropic / OpenAI / Gemini / xAI / Groq)

To execute with automated AI fact extraction and formal spec synthesis:

```bash
# Set your API token (either provider-specific or universal)
export TRACEPROOF_API_KEY="your-api-key"

# Phase 1: Configure with OpenAI (or anthropic, gemini, etc.)
traceproof-poc run \
  --source ./my_service/src \
  --docs ./my_service/docs \
  --out ./run_output \
  --provider openai \
  --model-strong gpt-4o \
  --model-cheap gpt-4o-mini \
  --scenario "Order service times out while payment webhook is in flight" \
  --fresh

# Phase 2: Index
traceproof-poc index --out ./run_output

# Phase 3: Generate
traceproof-poc generate --out ./run_output

# Phase 4: Verify & Export (requires tla-mcp installed)
traceproof-poc verify --out ./run_output
```

---

### Scenario C: Targeted Scenario Run

Scoping your run to a specific failure mode produces concise, checkable specifications:

```bash
traceproof-poc run \
  --source ./distributed_system \
  --scenario "Leader node crashes during uncommitted raft log replication" \
  --self-repair-cap 4 \
  --out .traceproof-poc \
  --fresh

traceproof-poc index --out .traceproof-poc
traceproof-poc generate --out .traceproof-poc
```

The module selection algorithm ranks all indexed modules and selects the top modules directly relevant to leader election, replication, and crashes.

---

### Scenario D: Resuming vs. Fresh Execution

By default, re-running `traceproof-poc run` with an existing `--out` directory will **resume**:

```bash
# Running without --fresh preserves existing configuration:
traceproof-poc run --source ./src --out .traceproof-poc
# Output: Resuming existing run — .traceproof-poc/run-config.md
```

To overwrite and reset configuration:
```bash
traceproof-poc run --source ./src --out .traceproof-poc --fresh
```

Because of incremental caching:
- Re-running `index` will print `Skipped (unchanged): <modules>` for any files whose content has not changed.
- Re-running `generate` will immediately reuse existing models if knowledge hashes have not changed.

---

## 7. Workspace Layout & Artifact Structure

### Directory Tree (`.traceproof-poc/`)

```text
.traceproof-poc/
├── run-config.md               # Frozen Phase 1 configuration
├── knowledge/                  # Phase 2 extracted knowledge
│   ├── _index.md               # Catalog of modules, paths, and SHA-256 hashes
│   ├── auth.md                 # Per-module extracted facts
│   └── database.md
├── model/                      # Phase 3 draft models & audit logs
│   ├── base.tla                # Synthesized TLA+ specification
│   ├── base.cfg                # TLC model checker parameters and invariants
│   └── generation-log.md       # Audit history of generation, prompt, and repair entries
└── export/                     # Phase 4 verified deliverables (created ONLY on pass)
    ├── base.tla                # Verified model copy
    ├── base.cfg                # Verified configuration copy
    └── manifest.md             # Verification outcome and downstream handoff record
```

### Artifact Specifications

#### `run-config.md`
Records the creation timestamp, source paths, docs paths, output directory, scenario, chosen provider, strong model, cheap model, and self-repair cap.

#### `knowledge/<module>.md`
Contains standardized sections:
- `## Summary`: High-level operational purpose.
- `## State`: Key variables and data structures.
- `## Initial Condition (sketch)`: Starting states.
- `## Candidate Invariants`: Safety and liveness properties to check.
- `## Scenario Relevance`: Interactions with the selected scenario.
- `## Conflicts`: Inconsistencies between documentation and code implementation.
- `## Open Questions`: Ambiguities flagged for human review.

#### `model/generation-log.md`
Appends chronological audit blocks for each generation and verification run, including timestamp, modules used, prompt snapshots, syntax pass/fail results, and self-repair attempts.

#### `export/manifest.md`
Final report summarizing:
- Timestamp and scenario.
- Knowledge files referenced.
- Syntax validation outcome (`PASS`).
- Model checking outcome (`PASS` or `COUNTEREXAMPLE FOUND`).
- Counterexample trace details if an invariant violation was discovered.
- Self-repair attempts consumed.

---

## 8. Programmatic Python API

All four phases can be called directly from Python:

```python
from pathlib import Path
from setup_phase import setup_run
from index_phase import index_run
from generate_phase import generate_run
from verify_phase import verify_run

out_dir = Path(".traceproof-poc")

# 1. Setup Phase
config_path = setup_run(
    source_paths=["./src"],
    docs_paths=["./docs"],
    output_dir=out_dir,
    provider="local",              # or "openai", "anthropic", etc.
    scenario="Worker fails during batch processing",
    self_repair_cap=3,
    fresh=True,
)
print(f"Config initialized: {config_path}")

# 2. Index Phase
index_path = index_run(output_dir=out_dir)
print(f"Index written: {index_path}")

# 3. Generate Phase
tla_path, cfg_path = generate_run(output_dir=out_dir)
print(f"Model generated: {tla_path} and {cfg_path}")

# 4. Verify Phase (using an injectable or default MCP client)
# Note: verify_run raises VerifyError if syntax validation or repair fails.
manifest_path = verify_run(output_dir=out_dir)
print(f"Verified model exported: {manifest_path}")
```

---

## 9. Running Automated Tests

The repository includes a comprehensive unit and integration test suite in `tests/test_phases.py` and `test_verify.py`.

The tests use simulated stub MCP clients (`StubMCPClient` and `FakeMcpClient`), allowing the entire pipeline to be validated without needing a live `tla-mcp` binary installed.

```bash
# Run the complete test suite
pytest

# Run with verbose output
pytest -v

# Run only Phase 4 verification tests
pytest test_verify.py -v
```

---

## 10. Troubleshooting & FAQs

### Q1: `error: --source path does not exist`
**Cause**: The directory or file supplied to `--source` could not be found or is not readable.  
**Resolution**: Check the relative or absolute path provided to `--source` and ensure read permissions are enabled (`chmod +r`).

### Q2: `warning: No credentials visible for provider '...'`
**Cause**: The selected provider was not `local`, but neither `{PROVIDER}_API_KEY` nor `TRACEPROOF_API_KEY` was found in your environment.  
**Resolution**: Set `export TRACEPROOF_API_KEY="your-token"` or use `--provider local` for offline execution.

### Q3: `error: No indexable files found under source paths`
**Cause**: The directory passed to `--source` contains only ignored folders (e.g. `.git`, `__pycache__`, `.venv`) or has no supported code/doc files.  
**Resolution**: Ensure `--source` points to a directory containing supported files (`.py`, `.go`, `.ts`, `.js`, `.java`, `.rs`, `.c`, `.cpp`, `.md`, etc.).

### Q4: `Skipped (unchanged): <module>` during indexing
**Cause**: Incremental caching detected that the module source files have identical SHA-256 hashes since the last extraction.  
**Resolution**: This is normal and saves API costs. To force re-indexing, pass `--fresh` to `traceproof-poc run` or delete `.traceproof-poc/knowledge/`.

### Q5: `tla-rs MCP server 'tla-mcp' not found` during `verify`
**Cause**: Phase 4 attempted to spawn the `tla-mcp` executable via stdio, but the binary was not located in your `$PATH`.  
**Resolution**:
1. Install `tla-rs`.
2. Point the environment variable to your binary:
   ```bash
   export TLA_RS_MCP_COMMAND="/path/to/tla-mcp"
   ```

### Q6: `Model check hit exploration limit (limit_reached)`
**Cause**: The model state graph exceeds default exploration constraints (`TLA_RS_MAX_STATES=1000`, `TLA_RS_MAX_DEPTH=30`, or `TLA_RS_MAX_SECONDS=15`).  
**Resolution**: Increase the exploration limits and re-run verify:
```bash
export TLA_RS_MAX_STATES=10000
export TLA_RS_MAX_DEPTH=100
export TLA_RS_MAX_SECONDS=60
traceproof-poc verify --out .traceproof-poc
```

### Q7: Verification reported `COUNTEREXAMPLE FOUND`. Is this a bug in the pipeline?
**Cause**: No! A counterexample indicates that TLC found a legitimate sequence of state transitions violating one of your candidate invariants.  
**Resolution**: This is a primary desired outcome of formal verification. The full trace is preserved in `export/manifest.md` for engineering analysis.
