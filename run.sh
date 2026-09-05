#!/bin/sh

source .venv/bin/activate
export TRACEPROOF_PROVIDER=google
export GOOGLE_API_KEY="AQ.Ab8RN6KIpdhQLkwdL77nlK9FEUKtNju9ZCOjvFMx1j38D7IRNQ"
# export ANTHROPIC_API_KEY=sk-RSFz3tLrPCM0HvaHeFXYK4R6tRaGiV6LLhL3hjGTUnhEUnjd
export TRACEPROOF_TLC_JAR=/home/fares/Specula/lib/tla2tools.jar
export TRACEPROOF_MODEL=gemini-3.7-flash
export ANTHROPIC_BASE_URL=https://kktoken.cc

python traceproof.py \
    --module DistCounter \
    --desc "Distributed counter where concurrent increments race on a shared variable" \
    examples/dist_counter/counter.py
