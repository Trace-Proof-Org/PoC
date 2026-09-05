# TraceProof PoC

**LLM-driven TLA+ spec generation and model checking for distributed/concurrent systems.**

TraceProof takes source code as input, uses an LLM (Claude) to generate a TLA+ specification, lints and self-repairs it, then runs TLC model checking and reports any invariant violations.

## Architecture

```
Source Code + Description
        │
        ▼
┌─────────────────────┐
│   Spec Generator    │  LLM → base.tla + MC.tla + MC.cfg
│  (agents/spec_gen)  │
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│    Repair Loop      │  SANY parse → LLM fix → repeat (≤5 iters)
│ (agents/repair_loop)│
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│   Model Checker     │  TLC → counterexample + statistics
│(agents/model_checker│
└────────┬────────────┘
         │
         ▼
    PipelineResult
  (violation report)
```

## Quick start

```bash
# Install dependencies
pip install anthropic

# Set your Anthropic API key
export ANTHROPIC_API_KEY=sk-ant-...

# Run on the built-in example (distributed counter with race condition)
python traceproof.py \
    --module DistCounter \
    --desc "Distributed counter where concurrent increments race on a shared variable" \
    examples/dist_counter/counter.py

# Or pipe inline code
echo "function increment() { x = x + 1; }" | \
    python traceproof.py --module Foo --desc "counter"
```

## Programmatic use

```python
from harness.pipeline import run

result = run(
    source_code=open("my_system.py").read(),
    module_name="MySystem",
    description="A distributed lock-free queue",
)
print(result.summary())

if result.tlc and result.tlc.violated:
    print("Bug found!", result.tlc.violated_invariant)
    for step in result.tlc.counterexample:
        print(f"  State {step['state']}: {step['action']}")
        for var, val in step['vars'].items():
            print(f"    {var} = {val}")
```

## Structure

| Path | Purpose |
|------|---------|
| `shared/schemas.py` | Core data types: `SpecDraft`, `LintResult`, `TLCResult`, `PipelineResult` |
| `shared/config.py` | Config from env vars (TLC jar path, LLM model, timeouts) |
| `shared/llm_client.py` | Anthropic API wrapper |
| `agents/spec_generator/` | LLM prompt + JSON extraction → `SpecDraft` |
| `agents/spec_generator/prompts/system.md` | System prompt for TLA+ spec generation |
| `agents/repair_loop/` | SANY lint → LLM fix loop → `LintResult` |
| `agents/model_checker/` | TLC invocation + output parsing → `TLCResult` |
| `harness/pipeline.py` | Orchestrates all three stages |
| `examples/dist_counter/` | Demo target: buggy distributed counter |

## Configuration

All config via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | *(required)* | Anthropic API key |
| `TRACEPROOF_MODEL` | `claude-opus-4-5` | LLM model |
| `TRACEPROOF_TLC_JAR` | *(required)* | Path to `tla2tools.jar` |
| `TRACEPROOF_TLC_TIMEOUT` | `300` | TLC timeout in seconds |
| `TRACEPROOF_TLC_WORKERS` | `auto` | TLC worker threads |
| `TRACEPROOF_TLC_MEMORY_MB` | `2048` | TLC JVM heap size |
| `TRACEPROOF_REPAIR_ITERS` | `5` | Max spec repair iterations |
| `TRACEPROOF_OUTPUT` | `runs/` | Output directory root |

## Relation to Specula

TraceProof shares Specula's goal (find bugs in distributed/concurrent systems using TLA+)
but takes a simpler, single-agent approach:

- No multi-phase pipeline (one LLM call generates the full spec)
- No harness generation or trace collection — purely model-checking driven
- No confirmation / classification phases
- Smaller, faster, easier to iterate on

Specula's skills and TLA+ patterns informed the spec-generation prompts and TLC invocation.
