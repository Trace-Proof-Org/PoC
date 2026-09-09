#!/usr/bin/env bash
set -euo pipefail

# ---------------- CONFIG (edit per run) ----------------------
SOURCE=("./examples/dist_counter")                    # one or more --source paths
DOCS=("./docs")                                       # one or more --docs paths (optional, can be empty array)
OUT=".traceproof-poc"                                 # output dir
PROVIDER="gemini"                                     # anthropic | openai | gemini | xai | groq | openrouter | ollama | local
MODEL_STRONG="gemini-3.7-flash"                       # e.g. claude-opus-4-5 (leave empty for provider default)
MODEL_CHEAP="gemini-3.6-flash"                        # e.g. claude-haiku-4-5 (leave empty for provider default)
MODEL_ADVERSARY="gemini-3.6-flash"                    # dedicated model for the Adversarial Critic Agent
SCENARIO=""                                           # e.g. "worker crashes while holding distributed lock"
SELF_REPAIR_CAP=3
NOTES=""                                              # freeform notes
FRESH=true                                            # true = overwrite existing run-config.md

# API key (only needed if PROVIDER != local)
export TRACEPROOF_API_KEY="YOUR_API_KEY_HERE"
# MCP / TLC limits (only used in verify phase)
export TLA_RS_MCP_COMMAND=$(which tla-mcp)
export TLA_RS_MAX_STATES=5000
export TLA_RS_MAX_DEPTH=100
export TLA_RS_MAX_SECONDS=30

# Which phases to run
RUN_SETUP=true
RUN_INDEX=true
RUN_GENERATE=true
RUN_TRACE_VALIDATE=true
RUN_VERIFY=true     # requires tla-mcp binary installed
RUN_ADVERSARY=true  # Adversarial Refinement Agent (Critic)
RUN_REPRODUCE=true  # Bug Confirmation Agent & Diagnostic Report
# ---------------------------------------------------------------

CLI="python3 cli.py"   # or: CLI="python3 cli.py"

build_run_args() {
  local args=()
  for s in "${SOURCE[@]}"; do args+=(--source "$s"); done
  for d in "${DOCS[@]:-}"; do [ -n "$d" ] && args+=(--docs "$d"); done
  args+=(--out "$OUT" --provider "$PROVIDER" --self-repair-cap "$SELF_REPAIR_CAP")
  [ -n "$MODEL_STRONG" ] && args+=(--model-strong "$MODEL_STRONG")
  [ -n "$MODEL_CHEAP" ] && args+=(--model-cheap "$MODEL_CHEAP")
  [ -n "$SCENARIO" ] && args+=(--scenario "$SCENARIO")
  [ -n "$NOTES" ] && args+=(--notes "$NOTES")
  [ "$FRESH" = true ] && args+=(--fresh)
  echo "${args[@]}"
}

echo "== TraceProof PoC run =="
echo "Provider: $PROVIDER | Out: $OUT | Scenario: ${SCENARIO:-<none>}"
echo

if [ "$RUN_SETUP" = true ]; then
  echo "-- Phase 1: run --"
  # shellcheck disable=SC2046
  $CLI run $(build_run_args)
  echo
fi

if [ "$RUN_INDEX" = true ]; then
  echo "-- Phase 2: index --"
  $CLI index --out "$OUT"
  echo
fi

if [ "$RUN_GENERATE" = true ]; then
  echo "-- Phase 3: generate --"
  $CLI generate --out "$OUT"
  echo
fi

if [ "${RUN_TRACE_VALIDATE:-true}" = true ]; then
  echo "-- Phase 4: trace validation (model-code conformance) --"
  $CLI trace-validate --source "${SOURCE[0]}" --out "$OUT"
  echo
fi

if [ "$RUN_VERIFY" = true ]; then
  echo "-- Phase 5: model checking (invariant exploration) --"
  $CLI verify --out "$OUT"
  echo
fi

if [ "${RUN_ADVERSARY:-true}" = true ]; then
  echo "-- Phase 6: adversarial refinement (critic) --"
  $CLI adversary --source "${SOURCE[0]}" --out "$OUT" --model "${MODEL_ADVERSARY:-$MODEL_STRONG}"
  echo
fi

if [ "${RUN_REPRODUCE:-true}" = true ]; then
  echo "-- Phase 7: bug confirmation & diagnostic report --"
  $CLI reproduce --source "${SOURCE[0]}" --out "$OUT"
  echo
fi

echo "== Done. Artifacts in $OUT/ =="