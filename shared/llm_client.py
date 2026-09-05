"""Provider-agnostic LLM client.

Supports multiple AI providers, selected via the TRACEPROOF_PROVIDER env var.

Usage:
    client = LLMClient()
    response = client.complete(system="...", user="...")

Supported providers
-------------------
anthropic (default)
    Requires: ANTHROPIC_API_KEY
    Optional: ANTHROPIC_BASE_URL  (override for proxies/compat endpoints)
    Model default: claude-opus-4-5

google
    Requires: GOOGLE_API_KEY
    Model default: gemini-2.5-pro
"""
from __future__ import annotations

import os
from typing import Optional

from shared import config


class LLMClient:
    """Provider-agnostic LLM client used by every agent stage."""

    def __init__(
        self,
        model: str = config.LLM_MODEL,
        max_tokens: int = config.LLM_MAX_TOKENS,
        temperature: float = config.LLM_TEMPERATURE,
        api_key: Optional[str] = None,
        provider: Optional[str] = None,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.provider = (provider or config.LLM_PROVIDER).lower()
        self._api_key = api_key or self._resolve_api_key()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def complete(self, *, system: str, user: str, json_mode: bool = False) -> str:
        """Make a single completion request and return the assistant text.

        Args:
            system: System / instruction prompt.
            user: User message.
            json_mode: When True, instruct the provider to return raw JSON with
                       no markdown or prose wrapping.  Currently enforced for
                       the Google provider via response_mime_type; ignored for
                       Anthropic (the prompt already handles it).
        """
        if self.provider == "anthropic":
            return self._complete_anthropic(system=system, user=user)
        if self.provider == "google":
            return self._complete_google(system=system, user=user, json_mode=json_mode)
        raise ValueError(
            f"Unknown LLM provider '{self.provider}'. "
            "Set TRACEPROOF_PROVIDER to 'anthropic' or 'google'."
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _resolve_api_key(self) -> str:
        if self.provider == "anthropic":
            return config.ANTHROPIC_API_KEY or os.environ.get("ANTHROPIC_API_KEY", "")
        if self.provider == "google":
            return config.GOOGLE_API_KEY or os.environ.get("GOOGLE_API_KEY", "")
        return ""

    def _complete_anthropic(self, *, system: str, user: str) -> str:
        try:
            import anthropic
        except ImportError as exc:
            raise RuntimeError("pip install anthropic") from exc

        base_url = config.ANTHROPIC_BASE_URL or os.environ.get("ANTHROPIC_BASE_URL")
        client_kwargs: dict = {"api_key": self._api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        client = anthropic.Anthropic(**client_kwargs)
        message = client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return message.content[0].text

    def _complete_google(self, *, system: str, user: str, json_mode: bool = False) -> str:
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise RuntimeError("pip install google-genai") from exc

        client = genai.Client(api_key=self._api_key)
        cfg_kwargs: dict = {
            "system_instruction": system,
            "max_output_tokens": self.max_tokens,
            "temperature": self.temperature,
        }
        if json_mode:
            cfg_kwargs["response_mime_type"] = "application/json"
        response = client.models.generate_content(
            model=self.model,
            contents=user,
            config=types.GenerateContentConfig(**cfg_kwargs),
        )
        return response.text
