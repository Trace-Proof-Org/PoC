#!/usr/bin/env bash
set -euo pipefail

# ---------------- CONFIG (edit per run) ----------------------
SOURCE=("./examples/disk_counter")                 # one or more --source paths
DOCS=("./docs")                  # one or more --docs paths (optional, can be empty array)
OUT=".traceproof-poc"            # output dir
PROVIDER="openrouter"                 # anthropic | openai | gemini | xai | groq | openrouter | ollama | local
MODEL_STRONG="openai/gpt-6-astra"                  # e.g. claude-opus-4-5 (leave empty for provider default)
MODEL_CHEAP="openai/gpt-6-astra"                   # e.g. claude-haiku-4-5 (leave empty for provider default)
SCENARIO=""                      # e.g. "worker crashes while holding distributed lock"
SELF_REPAIR_CAP=3
NOTES=""                         # freeform notes
FRESH=true                       # true = overwrite existing run-config.md

# API key (only needed if PROVIDER != local)
export TRACEPROOF_API_KEY="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"

# MCP / TLC limits (only used in verify phase)
export TLA_RS_MCP_COMMAND="/home/adham/.local/bin/tla-mcp"
export TLA_RS_MAX_STATES=5000
export TLA_RS_MAX_DEPTH=100
export TLA_RS_MAX_SECONDS=30

# Which phases to run
RUN_SETUP=true
RUN_INDEX=true
RUN_GENERATE=true
RUN_VERIFY=true     # requires tla-mcp binary installed
# ---------------------------------------------------------------

CLI="traceproof-poc"   # or: CLI="python3 cli.py"

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

if [ "$RUN_VERIFY" = true ]; then
  echo "-- Phase 4: verify --"
  $CLI verify --out "$OUT"
  echo
fi

echo "== Done. Artifacts in $OUT/ =="