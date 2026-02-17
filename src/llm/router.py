"""Model router — selects provider/model by purpose using config.

Review gate (HARD-PINNED, NON-NEGOTIABLE):
  purpose="review" ALWAYS resolves to OpenAIProvider + CODEX_REVIEW_MODEL (primary).
  If OpenAI returns quota/rate/5xx/timeout, falls back to the configured
  reviewFallback provider (e.g., Mistral Devstral Small 2).
  Both fail => fail-closed (RuntimeError with clear message).
  Missing OpenAI key => fail-closed (no silent downgrade to fallback-only).

  Budget cap is enforced before each call — review refused if estimate
  exceeds maxUsdPerReview (fail-closed).

For non-review purposes:
  Router checks config/llm.json defaults -> selects enabled provider.
  If the target provider is not enabled, falls through to OpenAI.
"""

from __future__ import annotations

import os
import sys
from typing import Any

from src.llm.config import load_llm_config, LLMConfigError
from src.llm.openai_provider import OpenAIProvider, CODEX_REVIEW_MODEL
from src.llm.mistral_provider import MistralProvider
from src.llm.moonshot_provider import MoonshotProvider
from src.llm.ollama_provider import OllamaProvider
from src.llm.provider import BaseProvider, _log
from src.llm.types import LLMConfig, LLMRequest, LLMResponse, ReviewFailClosedError
from src.llm.budget import (
    BudgetConfig,
    estimate_cost,
    check_budget,
    actual_cost,
    write_cost_telemetry,
    DEFAULT_PRICING,
)
from src.llm.cost_tracker import log_usage, check_guard

# Singleton router instance
_router_instance: ModelRouter | None = None

# HTTP status codes that trigger review fallback (transient/quota errors)
_REVIEW_FALLBACK_HTTP_CODES = {429, 500, 502, 503, 504}

# Transient classification for artifacts and doctor (exact rules)
TRANSIENT_QUOTA = "transient_quota"      # HTTP 429
TRANSIENT_SERVER = "transient_server"    # HTTP 500-504
TRANSIENT_NETWORK = "transient_network"  # connection/timeout
NON_TRANSIENT = "non_transient"


def _classify_transient(exc: RuntimeError) -> str:
    """Classify provider error for fallback and artifacts.

    Returns: transient_quota (429), transient_server (500-504),
    transient_network (timeout/unreachable), or non_transient.
    """
    msg = str(exc).lower()
    if "http 429" in msg:
        return TRANSIENT_QUOTA
    for code in (500, 502, 503, 504):
        if f"http {code}" in msg:
            return TRANSIENT_SERVER
    if "timeout" in msg or "unreachable" in msg or "timed out" in msg:
        return TRANSIENT_NETWORK
    return NON_TRANSIENT


def _log_usage_from_response(
    response: LLMResponse,
    action: str,
    project_id: str,
    pricing: dict[str, dict[str, float]] | None = None,
) -> None:
    """Append usage to cost tracker (always-on). No secrets."""
    usage = response.usage or {}
    if not usage:
        return
    try:
        cost = actual_cost(response.model, usage, pricing or DEFAULT_PRICING)
        log_usage(
            project_id=project_id,
            action=action,
            model=response.model,
            provider=response.provider,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
            cost_usd=cost,
        )
    except Exception:
        pass


def _is_transient_error(exc: RuntimeError) -> bool:
    """Check if a RuntimeError from a provider is a transient/quota error.

    Returns True for HTTP 429, 5xx, timeout, and unreachable errors.
    These are the only errors that trigger the review fallback path.
    """
    return _classify_transient(exc) != NON_TRANSIENT


class ModelRouter:
    """Routes LLM requests to the correct provider based on purpose.

    Invariants:
      1. purpose="review" -> OpenAIProvider + CODEX_REVIEW_MODEL (primary)
         - If OpenAI fails with transient error, try reviewFallback provider
         - Both fail -> fail-closed
      2. No silent fallback for review on auth/config errors (fail-closed)
      3. Non-review purposes use config defaults, with OpenAI as safe fallback
      4. Disabled providers are never called
      5. Budget cap enforced before each review call (fail-closed)
      6. Review caps (max_output_tokens, temperature) enforced on all review requests
    """

    def __init__(self, config: LLMConfig | None = None):
        """Initialize router with optional config.

        If config is None, loads from config/llm.json.
        If config loading fails, falls back to OpenAI-only mode.
        """
        self._config = config
        self._providers: dict[str, BaseProvider] = {}
        self._init_error: str | None = None
        self._budget: BudgetConfig = BudgetConfig()

        if config is None:
            try:
                self._config = load_llm_config()
            except LLMConfigError as exc:
                self._init_error = str(exc)
                _log(f"Config load failed (using OpenAI-only): {exc}")
                self._config = LLMConfig()  # Defaults: openai only

        # Load budget config
        if self._config and self._config.budget_config:
            self._budget = BudgetConfig.from_dict(self._config.budget_config)

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

        # Mistral — always initialize if in config (needed for review fallback)
        mistral_cfg = config.providers.get("mistral")
        if mistral_cfg:
            mistral_base = mistral_cfg.api_base if mistral_cfg.api_base else ""
            try:
                self._providers["mistral"] = (
                    MistralProvider(api_base=mistral_base)
                    if mistral_base
                    else MistralProvider()
                )
            except Exception as exc:
                _log(f"Mistral provider init failed: {exc}")

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
        # HARD-PINNED: review always goes to OpenAI (primary)
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
            # Fail-closed: refuse gpt-4o unless explicit override (cost guard)
            if CODEX_REVIEW_MODEL == "gpt-4o" and os.environ.get("OPENCLAW_ALLOW_EXPENSIVE_REVIEW") != "1":
                raise RuntimeError(
                    "Review gate is set to gpt-4o (expensive). "
                    "Set OPENCLAW_ALLOW_EXPENSIVE_REVIEW=1 to allow, or use gpt-4o-mini (default). Fail-closed."
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
        default_model = "gpt-4o-mini"
        if route:
            default_model = route.model
        elif "general" in config.defaults:
            default_model = config.defaults["general"].model

        return openai, default_model

    def resolve_review_fallback(self) -> tuple[BaseProvider, str] | None:
        """Resolve the review fallback provider, if configured.

        Returns None if no fallback is configured or the fallback
        provider is not available/configured.
        """
        config = self._config
        assert config is not None

        fb = config.review_fallback
        if not fb or not fb.provider or not fb.model:
            return None

        provider = self._providers.get(fb.provider)
        if provider is None:
            _log(
                f"Review fallback provider '{fb.provider}' not initialized"
            )
            return None

        if not provider.is_configured():
            _log(
                f"Review fallback provider '{fb.provider}' not configured "
                f"(missing API key?)"
            )
            return None

        return provider, fb.model

    def generate(self, request: LLMRequest) -> LLMResponse:
        """Route and execute an LLM request based on purpose.

        For review purpose:
          1. Enforce review caps (max_output_tokens, temperature)
          2. Check budget cap
          3. Try OpenAI (primary)
          4. If transient error + fallback configured, try fallback
          5. Both fail -> raise (fail-closed)
          6. Record provenance

        For non-review:
          Standard routing via resolve().
        """
        config = self._config
        assert config is not None

        # Cost guard: block non-essential LLM when hourly/daily limit exceeded
        if not getattr(request, "essential", False):
            allowed, reason = check_guard(run_id=request.trace_id or "")
            if not allowed:
                raise RuntimeError(
                    f"COST_GUARD_TRIPPED: {reason}. "
                    "Non-essential LLM blocked. Doctor/deploy/guard remain available."
                )

        if request.purpose == "review":
            return self._generate_review(request)

        provider, model = self.resolve(request.purpose)

        routed_request = LLMRequest(
            model=model,
            messages=request.messages,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            purpose=request.purpose,
            trace_id=request.trace_id,
            response_format=request.response_format,
            essential=getattr(request, "essential", False),
        )

        response = provider.generate_text(routed_request)
        _log_usage_from_response(response, request.purpose, "openclaw", self._budget.pricing)
        return response

    def _generate_review(self, request: LLMRequest) -> LLMResponse:
        """Execute a review request with fallback and budget enforcement.

        Flow:
          1. Apply review caps (max_output_tokens from config)
          2. Estimate cost + check budget cap (fail-closed)
          3. Call OpenAI (primary)
          4. On transient error: try fallback provider
          5. On success: add fallback provenance to response if used
        """
        config = self._config
        assert config is not None
        caps = config.review_caps

        # Resolve primary
        primary_provider, primary_model = self.resolve("review")

        # Apply review caps
        max_tokens = caps.max_output_tokens
        temperature = caps.temperature

        # Build prompt text for cost estimation
        prompt_text = ""
        for msg in request.messages:
            prompt_text += msg.get("content", "")

        # Budget check (fail-closed for review gate)
        est = estimate_cost(
            model=primary_model,
            prompt_text=prompt_text,
            max_output_tokens=max_tokens,
            pricing=self._budget.pricing,
            provider="openai",
        )
        allowed, reason = check_budget(
            est, self._budget.max_usd_per_review, "review"
        )
        if not allowed:
            raise RuntimeError(
                f"BUDGET EXCEEDED (fail-closed): {reason}. "
                f"Increase budget.maxUsdPerReview in config/llm.json "
                f"or reduce bundle size."
            )

        _log(
            f"Review budget check: {reason} "
            f"(est ~${est.estimated_cost_usd:.4f})",
            trace_id=request.trace_id,
        )

        # Build the routed request with enforced caps
        routed_request = LLMRequest(
            model=primary_model,
            messages=request.messages,
            temperature=temperature,
            max_tokens=max_tokens,
            purpose="review",
            trace_id=request.trace_id,
            response_format=request.response_format,
        )

        # Try primary (OpenAI)
        primary_error: RuntimeError | None = None
        primary_transient_class: str = NON_TRANSIENT
        try:
            response = primary_provider.generate_text(routed_request)
            _log_usage_from_response(response, "review", "openclaw", self._budget.pricing)
            return response
        except RuntimeError as exc:
            primary_error = exc
            primary_transient_class = _classify_transient(exc)
            if primary_transient_class == NON_TRANSIENT:
                # Auth, config, schema, etc. -> fail-closed immediately
                raise

        # Primary failed with transient error -> try fallback
        _log(
            f"Review primary (OpenAI) failed with transient error "
            f"({primary_transient_class}). Trying fallback...",
            trace_id=request.trace_id,
        )

        fallback = self.resolve_review_fallback()
        if fallback is None:
            raise RuntimeError(
                f"Review primary (OpenAI) failed: {primary_error}. "
                f"No fallback reviewer configured. "
                f"Set reviewFallback in config/llm.json + MISTRAL_API_KEY."
            ) from primary_error

        fallback_provider, fallback_model = fallback

        # Budget check for fallback model
        fb_est = estimate_cost(
            model=fallback_model,
            prompt_text=prompt_text,
            max_output_tokens=max_tokens,
            pricing=self._budget.pricing,
            provider=fallback_provider.provider_name,
        )
        fb_allowed, fb_reason = check_budget(
            fb_est, self._budget.max_usd_per_review, "review_fallback"
        )
        if not fb_allowed:
            raise RuntimeError(
                f"BUDGET EXCEEDED for fallback (fail-closed): {fb_reason}"
            ) from primary_error

        # Build fallback request
        fb_request = LLMRequest(
            model=fallback_model,
            messages=request.messages,
            temperature=temperature,
            max_tokens=max_tokens,
            purpose="review",
            trace_id=request.trace_id,
            response_format=request.response_format,
        )

        try:
            response = fallback_provider.generate_text(fb_request)
            # Tag response with fallback provenance and classification
            response.primary_transient_class = primary_transient_class
            _log_usage_from_response(response, "review_fallback", "openclaw", self._budget.pricing)
            _log(
                f"Review fallback ({fallback_provider.provider_name}/"
                f"{fallback_model}) succeeded",
                trace_id=request.trace_id,
            )
            return response
        except RuntimeError as fb_exc:
            # Both failed -> fail-closed with structured error for artifacts
            from src.llm.provider import redact_for_log
            primary_msg = redact_for_log(str(primary_error))
            fallback_msg = redact_for_log(str(fb_exc))
            raise ReviewFailClosedError(
                f"Review FAILED (fail-closed): "
                f"primary (OpenAI) error: {primary_msg}; "
                f"fallback ({fallback_provider.provider_name}) error: {fallback_msg}",
                primary_error=primary_msg,
                fallback_error=fallback_msg,
                primary_transient_class=primary_transient_class,
            ) from fb_exc

    def get_all_status(self) -> list[dict[str, Any]]:
        """Return status for all known providers (for HQ status endpoint).

        Never exposes secrets. Shows enabled/disabled, configured, fingerprint.
        """
        config = self._config
        assert config is not None

        statuses = []
        for pname in ["openai", "mistral", "moonshot", "ollama"]:
            provider = self._providers.get(pname)
            if provider:
                status = provider.get_status()
                status["enabled"] = pname in config.enabled_providers
            else:
                # Provider not initialized — show as disabled
                status = {
                    "name": {
                        "openai": "OpenAI",
                        "mistral": "Mistral",
                        "moonshot": "Moonshot (Kimi)",
                        "ollama": "Ollama (Local)",
                    }.get(pname, pname),
                    "enabled": pname in config.enabled_providers,
                    "configured": False,
                    "status": "disabled",
                    "fingerprint": None,
                }
            statuses.append(status)

        # Add review fallback info
        fb = config.review_fallback
        if fb and fb.provider:
            for s in statuses:
                if s.get("name", "").lower().startswith(fb.provider[:4]):
                    s["review_fallback"] = True
                    s["review_fallback_model"] = fb.model

        # Add budget info
        budget_info = {
            "max_usd_per_review": self._budget.max_usd_per_review,
            "max_usd_per_run": self._budget.max_usd_per_run,
        }

        return statuses

    @property
    def budget(self) -> BudgetConfig:
        """Return budget config (for external cost checks)."""
        return self._budget

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
