"""Configuration for the TraceProof pipeline.

Reads from environment variables with sensible defaults.
Override by setting env vars before running.

Provider selection
------------------
Set TRACEPROOF_PROVIDER to one of:
  - "anthropic"  (default) — uses ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL
  - "google"               — uses GOOGLE_API_KEY
"""
from __future__ import annotations

import os
from pathlib import Path

# ── TLC ──────────────────────────────────────────────────────────────────
_DEFAULT_JAR = "tla2tools.jar"

TLC_JAR: str = os.environ.get("TRACEPROOF_TLC_JAR", _DEFAULT_JAR)
TLC_MEMORY_MB: int = int(os.environ.get("TRACEPROOF_TLC_MEMORY_MB", "2048"))
TLC_TIMEOUT_SECONDS: int = int(os.environ.get("TRACEPROOF_TLC_TIMEOUT", "300"))  # 5 min default
TLC_WORKERS: str = os.environ.get("TRACEPROOF_TLC_WORKERS", "auto")

# ── LLM provider ─────────────────────────────────────────────────────────
LLM_PROVIDER: str = os.environ.get("TRACEPROOF_PROVIDER", "anthropic").lower()

# Anthropic
ANTHROPIC_API_KEY: str = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_BASE_URL: str = os.environ.get("ANTHROPIC_BASE_URL", "")

# Google AI (Gemini)
GOOGLE_API_KEY: str = os.environ.get("GOOGLE_API_KEY", "")

# ── LLM model / generation params ────────────────────────────────────────
_DEFAULT_MODELS: dict[str, str] = {
    "anthropic": "claude-opus-4-5",
    "google": "gemini-2.5-pro",
}
LLM_MODEL: str = (
    os.environ.get("TRACEPROOF_MODEL")
    or _DEFAULT_MODELS.get(LLM_PROVIDER, "claude-opus-4-5")
)
LLM_MAX_TOKENS: int = int(os.environ.get("TRACEPROOF_MAX_TOKENS", "8192"))
LLM_TEMPERATURE: float = float(os.environ.get("TRACEPROOF_TEMPERATURE", "0.2"))

# ── Repair loop ───────────────────────────────────────────────────────────
REPAIR_MAX_ITERATIONS: int = int(os.environ.get("TRACEPROOF_REPAIR_ITERS", "5"))

# ── Output ────────────────────────────────────────────────────────────────
OUTPUT_ROOT: Path = Path(os.environ.get("TRACEPROOF_OUTPUT", "runs"))
