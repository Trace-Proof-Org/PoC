#!/usr/bin/env bash
set -euo pipefail

# ---------------- CONFIG (edit per run) ----------------------
SOURCE=("./examples/dist_lock")                    # one or more --source paths
DOCS=("./docs")                  # one or more --docs paths (optional, can be empty array)
OUT=".traceproof-poc"            # output dir
PROVIDER="gemini"                # anthropic | openai | gemini | xai | groq | openrouter | ollama | local
MODEL_STRONG="gemini-3.7-flash"               # e.g. claude-opus-4-5 (leave empty for provider default)
MODEL_CHEAP="gemini-3.6-flash"                  # e.g. claude-haiku-4-5 (leave empty for provider default)
MODEL_ADVERSARY="gemini-3.6-flash"              # dedicated model for the Adversarial Critic Agent
SCENARIO=""                      # e.g. "worker crashes while holding distributed lock"
SELF_REPAIR_CAP=5
NOTES=""                         # freeform notes
FRESH=true                       # true = overwrite existing run-config.md

# API key (only needed if PROVIDER != local)
export TRACEPROOF_API_KEY="YOUR_API_KEY_HERE"
# MCP / TLC limits (only used in verify phase)
export TLA_RS_MCP_COMMAND="/usr/local/bin/tla-mcp"
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

phase_banner() {
  local num="$1"
  local name="$2"
  echo -e "\033[1;36m┌──────────────────────────────────────────────────────────────────────────────┐\033[0m"
  echo -e "\033[1;36m│ [PHASE $num] $name\033[0m"
  echo -e "\033[1;36m└──────────────────────────────────────────────────────────────────────────────┘\033[0m"
}

echo -e "\033[1;35m================================================================================\033[0m"
echo -e "\033[1;35m  TraceProof Formal Verification & Bug Reproduction Pipeline                    \033[0m"
echo -e "\033[1;35m================================================================================\033[0m"
echo -e "  • Provider:  \033[1m$PROVIDER\033[0m (Strong: ${MODEL_STRONG:-default}, Cheap: ${MODEL_CHEAP:-default})"
echo -e "  • Output:    \033[1m$OUT\033[0m"
echo -e "  • Scenario:  \033[1m${SCENARIO:-<module-scoped run>}\033[0m"
echo -e "  • Target(s): \033[1m${SOURCE[*]}\033[0m"
echo

if [ "$RUN_SETUP" = true ]; then
  phase_banner "1/7" "Run Setup & Configuration Ingestion"
  # shellcheck disable=SC2046
  $CLI run $(build_run_args)
  echo
fi

if [ "$RUN_INDEX" = true ]; then
  phase_banner "2/7" "Codebase & Documentation AST Indexing"
  $CLI index --out "$OUT"
  echo
fi

if [ "$RUN_GENERATE" = true ]; then
  phase_banner "3/7" "TLA+ Model Drafting & Invariant Generation"
  $CLI generate --out "$OUT"
  echo
fi

if [ "${RUN_TRACE_VALIDATE:-true}" = true ]; then
  phase_banner "4/7" "Trace Conformance Validation (Model-Code Trace Check)"
  $CLI trace-validate --source "${SOURCE[0]}" --out "$OUT"
  echo
fi

if [ "$RUN_VERIFY" = true ]; then
  phase_banner "5/7" "Formal Verification & Non-Vacuity Gatekeeper (TLC / tla-rs)"
  $CLI verify --out "$OUT"
  echo
fi

if [ "${RUN_ADVERSARY:-true}" = true ]; then
  phase_banner "6/7" "Adversarial Refinement (Critic Agent)"
  $CLI adversary --source "${SOURCE[0]}" --out "$OUT" --model "${MODEL_ADVERSARY:-$MODEL_STRONG}"
  echo
fi

if [ "${RUN_REPRODUCE:-true}" = true ]; then
  phase_banner "7/7" "Runtime Bug Reproducer & Final Diagnostic Report"
  $CLI reproduce --source "${SOURCE[0]}" --out "$OUT"
  echo
fi

echo -e "\033[1;32m================================================================================\033[0m"
echo -e "\033[1;32m  ✔ TraceProof Pipeline Execution Complete                                      \033[0m"
echo -e "\033[1;32m================================================================================\033[0m"
echo -e "  • Run Config:   \033[1m$OUT/run-config.md\033[0m"
echo -e "  • Model Files:  \033[1m$OUT/model/base.tla\033[0m, \033[1m$OUT/model/base.cfg\033[0m"
echo -e "  • Manifest:     \033[1m$OUT/export/manifest.md\033[0m"
[ -f "$OUT/reports/diagnostic-report.md" ] && echo -e "  • Diagnostics:  \033[1m$OUT/reports/diagnostic-report.md\033[0m"
echo