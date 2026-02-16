"""Model router — selects provider/model by purpose using config.

Review gate invariant (HARD-PINNED, NON-NEGOTIABLE):
  purpose="review" ALWAYS resolves to OpenAIProvider + CODEX_REVIEW_MODEL.
  No fallback, no override, no config bypass.
  Missing OpenAI key => fail-closed (RuntimeError with clear message).

For non-review purposes:
  Router checks config/llm.json defaults -> selects enabled provider.
  If the target provider is not enabled, falls through to OpenAI.
"""

from __future__ import annotations

import sys
from typing import Any

from src.llm.config import load_llm_config, LLMConfigError
from src.llm.openai_provider import OpenAIProvider, CODEX_REVIEW_MODEL
from src.llm.moonshot_provider import MoonshotProvider
from src.llm.ollama_provider import OllamaProvider
from src.llm.provider import BaseProvider, _log
from src.llm.types import LLMConfig, LLMRequest, LLMResponse

# Singleton router instance
_router_instance: ModelRouter | None = None


class ModelRouter:
    """Routes LLM requests to the correct provider based on purpose.

    Invariants:
      1. purpose="review" -> OpenAIProvider + CODEX_REVIEW_MODEL (always)
      2. No silent fallback for review (fail-closed)
      3. Non-review purposes use config defaults, with OpenAI as safe fallback
      4. Disabled providers are never called
    """

    def __init__(self, config: LLMConfig | None = None):
        """Initialize router with optional config.

        If config is None, loads from config/llm.json.
        If config loading fails, falls back to OpenAI-only mode.
        """
        self._config = config
        self._providers: dict[str, BaseProvider] = {}
        self._init_error: str | None = None

        if config is None:
            try:
                self._config = load_llm_config()
            except LLMConfigError as exc:
                self._init_error = str(exc)
                _log(f"Config load failed (using OpenAI-only): {exc}")
                self._config = LLMConfig()  # Defaults: openai only

        self._setup_providers()

    def _setup_providers(self) -> None:
        """Initialize provider instances based on config."""
        config = self._config
        assert config is not None

        # OpenAI is ALWAYS available (review gate requires it)
        openai_cfg = config.providers.get("openai")
        openai_base = openai_cfg.api_base if openai_cfg and openai_cfg.api_base else ""
        self._providers["openai"] = (
            OpenAIProvider(api_base=openai_base)
            if openai_base
            else OpenAIProvider()
        )

        # Moonshot — only if enabled in config
        if "moonshot" in config.enabled_providers:
            moonshot_cfg = config.providers.get("moonshot")
            moonshot_base = (
                moonshot_cfg.api_base
                if moonshot_cfg and moonshot_cfg.api_base
                else ""
            )
            try:
                self._providers["moonshot"] = (
                    MoonshotProvider(api_base=moonshot_base)
                    if moonshot_base
                    else MoonshotProvider()
                )
            except Exception as exc:
                _log(f"Moonshot provider init failed: {exc}")

        # Ollama — only if enabled in config
        if "ollama" in config.enabled_providers:
            ollama_cfg = config.providers.get("ollama")
            ollama_base = (
                ollama_cfg.api_base
                if ollama_cfg and ollama_cfg.api_base
                else ""
            )
            try:
                self._providers["ollama"] = (
                    OllamaProvider(api_base=ollama_base)
                    if ollama_base
                    else OllamaProvider()
                )
            except ValueError as exc:
                _log(f"Ollama provider init failed (not localhost?): {exc}")

    def resolve(self, purpose: str) -> tuple[BaseProvider, str]:
        """Resolve provider + model for a given purpose.

        Returns
        -------
        tuple[BaseProvider, str]
            (provider_instance, model_name)

        Raises
        ------
        RuntimeError
            If purpose=review and OpenAI is not configured (fail-closed).
        """
        # HARD-PINNED: review always goes to OpenAI
        if purpose == "review":
            openai = self._providers.get("openai")
            if openai is None:
                raise RuntimeError(
                    "FATAL: OpenAI provider not initialized. "
                    "Review gate requires OpenAI (fail-closed)."
                )
            if not openai.is_configured():
                raise RuntimeError(
                    "FATAL: OpenAI API key not found. "
                    "Review gate requires OpenAI API key (fail-closed). "
                    "Set OPENAI_API_KEY or run: python3 ops/openai_key.py set"
                )
            return openai, CODEX_REVIEW_MODEL

        # Non-review: check config defaults
        config = self._config
        assert config is not None

        route = config.defaults.get(purpose)
        if route:
            provider = self._providers.get(route.provider)
            if provider and route.provider in config.enabled_providers:
                return provider, route.model

        # Fallback: OpenAI for any purpose
        openai = self._providers.get("openai")
        if openai is None:
            raise RuntimeError(
                f"No provider available for purpose '{purpose}'"
            )

        # Use config default model for general, or a sensible default
        default_model = "gpt-4o"
        if route:
            default_model = route.model
        elif "general" in config.defaults:
            default_model = config.defaults["general"].model

        return openai, default_model

    def generate(self, request: LLMRequest) -> LLMResponse:
        """Route and execute an LLM request based on purpose.

        Convenience method that resolves provider + model, then calls generate_text.
        """
        provider, model = self.resolve(request.purpose)

        # Override model with the resolved one (purpose-based routing)
        routed_request = LLMRequest(
            model=model,
            messages=request.messages,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            purpose=request.purpose,
            trace_id=request.trace_id,
            response_format=request.response_format,
        )

        return provider.generate_text(routed_request)

    def get_all_status(self) -> list[dict[str, Any]]:
        """Return status for all known providers (for HQ status endpoint).

        Never exposes secrets. Shows enabled/disabled, configured, fingerprint.
        """
        config = self._config
        assert config is not None

        statuses = []
        for pname in ["openai", "moonshot", "ollama"]:
            provider = self._providers.get(pname)
            if provider:
                status = provider.get_status()
                status["enabled"] = pname in config.enabled_providers
            else:
                # Provider not initialized — show as disabled
                status = {
                    "name": {
                        "openai": "OpenAI",
                        "moonshot": "Moonshot (Kimi)",
                        "ollama": "Ollama (Local)",
                    }.get(pname, pname),
                    "enabled": pname in config.enabled_providers,
                    "configured": False,
                    "status": "disabled",
                    "fingerprint": None,
                }
            statuses.append(status)

        return statuses

    @property
    def init_error(self) -> str | None:
        """Return config init error message, if any."""
        return self._init_error


def get_router(config: LLMConfig | None = None) -> ModelRouter:
    """Get or create the singleton ModelRouter.

    Thread-safe: uses module-level singleton.
    If config is provided, creates a new router (useful for testing).
    """
    global _router_instance
    if config is not None:
        # Explicit config: create new router (testing scenario)
        return ModelRouter(config=config)
    if _router_instance is None:
        _router_instance = ModelRouter()
    return _router_instance


def reset_router() -> None:
    """Reset the singleton router (for testing only)."""
    global _router_instance
    _router_instance = None
