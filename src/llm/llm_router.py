"""Central LLM router — single entrypoint for ALL LLM calls in ai-ops-runner.

Uses "logical roles" to abstract away provider/model details:

  - core_brain:   default for planning, general Q&A, and complex reasoning
  - review_brain: security-focused code/diff review (hard-pinned to OpenAI)
  - doctor_brain: lightweight health checks and diagnostics
  - fast_helper:  cheap/fast model for simple tasks (shares config with core_brain
                  unless overridden in config/llm.json defaults.fast_helper)

Roles are mapped to providers/models via config/llm.json defaults.
Call sites should never reference provider names or model IDs directly.

Usage::

    from src.llm.llm_router import generate, CORE_BRAIN, REVIEW_BRAIN

    response = generate(
        role=CORE_BRAIN,
        messages=[{"role": "user", "content": "Hello"}],
        trace_id="my_trace",
    )
    print(response.content)

Error taxonomy:
    LLMRouterError          – base class for all router errors
      ConfigError           – provider/config misconfigured or missing key
      AuthError             – authentication/authorization failure (HTTP 401/403)
      RateLimitError        – rate limit / quota exceeded (HTTP 429)
      TransientError        – transient network/server issue (timeout, 5xx)
    ReviewFailClosedError   – both primary and fallback reviewers failed

All errors carry ``error_code`` and ``role`` attributes for structured handling.
"""

from __future__ import annotations

import datetime
import json
import sys
from typing import Any

from src.llm.router import ModelRouter, get_router as _get_router, _classify_transient
from src.llm.types import LLMRequest, LLMResponse, ReviewFailClosedError
from src.llm.provider import _log, redact_for_log

# ---------------------------------------------------------------------------
# Logical role constants
# ---------------------------------------------------------------------------

CORE_BRAIN: str = "core_brain"
REVIEW_BRAIN: str = "review_brain"
DOCTOR_BRAIN: str = "doctor_brain"
FAST_HELPER: str = "fast_helper"

ALL_ROLES: frozenset[str] = frozenset({CORE_BRAIN, REVIEW_BRAIN, DOCTOR_BRAIN, FAST_HELPER})

_ROLE_TO_PURPOSE: dict[str, str] = {
    CORE_BRAIN: "general",
    REVIEW_BRAIN: "review",
    DOCTOR_BRAIN: "doctor",
    FAST_HELPER: "fast_helper",
}


# ---------------------------------------------------------------------------
# Error taxonomy
# ---------------------------------------------------------------------------

class LLMRouterError(RuntimeError):
    """Base error for LLM router failures."""

    def __init__(self, message: str, *, error_code: str = "unknown", role: str = ""):
        super().__init__(message)
        self.error_code = error_code
        self.role = role


class ConfigError(LLMRouterError):
    """Provider/config is misconfigured or API key is missing."""

    def __init__(self, message: str, role: str = ""):
        super().__init__(message, error_code="config_error", role=role)


class AuthError(LLMRouterError):
    """Authentication/authorization failure (HTTP 401/403)."""

    def __init__(self, message: str, role: str = ""):
        super().__init__(message, error_code="auth_error", role=role)


class RateLimitError(LLMRouterError):
    """Rate limit / quota exceeded (HTTP 429)."""

    def __init__(self, message: str, role: str = ""):
        super().__init__(message, error_code="rate_limit", role=role)


class TransientError(LLMRouterError):
    """Transient network/server issue (timeout, HTTP 5xx)."""

    def __init__(self, message: str, role: str = ""):
        super().__init__(message, error_code="transient", role=role)


# ---------------------------------------------------------------------------
# Observability — structured metadata-only logging
# ---------------------------------------------------------------------------

def _log_call(
    role: str,
    provider: str,
    model: str,
    success: bool,
    error_code: str | None,
    usage: dict[str, int] | None,
    trace_id: str,
) -> None:
    """Log metadata for an LLM call. Never logs prompts, responses, or secrets."""
    entry: dict[str, Any] = {
        "ts": datetime.datetime.utcnow().isoformat() + "Z",
        "role": role,
        "provider": provider,
        "model": model,
        "ok": success,
    }
    if error_code:
        entry["err"] = error_code
    if usage:
        entry["prompt_tok"] = usage.get("prompt_tokens", 0)
        entry["compl_tok"] = usage.get("completion_tokens", 0)
        entry["total_tok"] = usage.get("total_tokens", 0)
    _log(f"router_call {json.dumps(entry, separators=(',', ':'))}", trace_id=trace_id)


def _classify_to_error(exc: RuntimeError, role: str) -> LLMRouterError:
    """Wrap a raw RuntimeError into the appropriate LLMRouterError subclass."""
    cls = _classify_transient(exc)
    msg = str(exc)
    if cls == "transient_quota":
        return RateLimitError(msg, role=role)
    if cls in ("transient_server", "transient_network"):
        return TransientError(msg, role=role)
    lower = msg.lower()
    if "401" in lower or "403" in lower or "unauthorized" in lower or "forbidden" in lower:
        return AuthError(msg, role=role)
    if "not found" in lower or "not configured" in lower or "missing" in lower:
        return ConfigError(msg, role=role)
    return LLMRouterError(msg, error_code="unknown", role=role)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate(
    role: str,
    messages: list[dict[str, Any]],
    *,
    trace_id: str | None = None,
    project_id: str = "openclaw",
    action: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    response_format: dict[str, str] | None = None,
    essential: bool = False,
    provider_override: str | None = None,
    model_override: str | None = None,
) -> LLMResponse:
    """Central entrypoint for all LLM calls in the repo.

    Parameters
    ----------
    role
        Logical role: ``CORE_BRAIN``, ``REVIEW_BRAIN``, ``DOCTOR_BRAIN``,
        or ``FAST_HELPER``.
    messages
        Chat messages in OpenAI format (list of ``{"role": ..., "content": ...}``).
    trace_id
        Correlation ID for logging and cost attribution.
    project_id
        Project identifier for cost attribution.
    action
        Action name for cost attribution (defaults to the resolved purpose).
    temperature
        Sampling temperature. Review role enforces caps from config.
    max_tokens
        Max output tokens. Review role enforces caps from config.
    response_format
        Response format hint (e.g. ``{"type": "json_object"}``).
    essential
        If ``True``, bypasses the cost guard (for doctor/deploy/guard calls).
    provider_override
        Force a specific provider (for doctor health checks that need to
        test a specific provider independently).
    model_override
        Force a specific model (used with ``provider_override``).

    Returns
    -------
    LLMResponse
        Response with ``content``, ``model``, ``provider``, ``usage`` metadata.

    Raises
    ------
    LLMRouterError
        On any failure. Subclasses: ``ConfigError``, ``AuthError``,
        ``RateLimitError``, ``TransientError``.
    ReviewFailClosedError
        When both primary and fallback reviewers fail (review_brain only).
    """
    if role not in ALL_ROLES:
        raise ConfigError(
            f"Unknown role: '{role}'. Valid roles: {sorted(ALL_ROLES)}",
            role=role,
        )

    purpose = _ROLE_TO_PURPOSE[role]
    trace = trace_id or ""
    act = action or purpose
    router = _get_router()

    req = LLMRequest(
        model=model_override or "",
        messages=messages,
        temperature=temperature if temperature is not None else 0.0,
        max_tokens=max_tokens,
        purpose=purpose,
        trace_id=trace,
        response_format=response_format,
        essential=essential,
        project_id=project_id,
        action=act,
    )

    if provider_override:
        return _generate_with_provider_override(
            router, req, role, provider_override, model_override or "", trace
        )

    try:
        response = router.generate(req)
        _log_call(role, response.provider, response.model, True, None, response.usage, trace)
        return response
    except ReviewFailClosedError:
        _log_call(role, "unknown", "", False, "review_fail_closed", None, trace)
        raise
    except RuntimeError as exc:
        cls = _classify_transient(exc)
        _log_call(role, "unknown", "", False, cls, None, trace)
        raise


def _generate_with_provider_override(
    router: ModelRouter,
    request: LLMRequest,
    role: str,
    provider_name: str,
    model: str,
    trace_id: str,
) -> LLMResponse:
    """Call a specific provider directly (for doctor health checks)."""
    provider = router.get_provider(provider_name)
    if provider is None:
        raise ConfigError(
            f"Provider '{provider_name}' not initialized",
            role=role,
        )
    if not provider.is_configured():
        raise ConfigError(
            f"Provider '{provider_name}' not configured (missing API key?)",
            role=role,
        )

    if model:
        request = LLMRequest(
            model=model,
            messages=request.messages,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            purpose=request.purpose,
            trace_id=request.trace_id,
            response_format=request.response_format,
            essential=request.essential,
            project_id=request.project_id,
            action=request.action,
        )

    try:
        response = provider.generate_text(request)
        _log_call(role, response.provider, response.model, True, None, response.usage, trace_id)
        return response
    except RuntimeError as exc:
        cls = _classify_transient(exc)
        _log_call(role, provider_name, model, False, cls, None, trace_id)
        raise


def resolve_provider_model(role: str) -> tuple[str, str]:
    """Resolve ``(provider_name, model_name)`` for a logical role.

    Useful for diagnostics and config verification without making an API call.
    """
    if role not in ALL_ROLES:
        raise ConfigError(
            f"Unknown role: '{role}'. Valid roles: {sorted(ALL_ROLES)}",
            role=role,
        )
    purpose = _ROLE_TO_PURPOSE[role]
    router = _get_router()
    provider, model = router.resolve(purpose)
    return provider.provider_name, model


def check_provider_health(
    provider_name: str,
    model: str,
) -> tuple[str, str | None]:
    """Run a minimal LLM call to verify provider reachability.

    Returns ``(state, error_class)`` where:
      - state: ``"OK"``, ``"DEGRADED"``, or ``"DOWN"``
      - error_class: ``None`` on success, or a classification string on failure

    Used by ``src/llm/doctor.py`` for health checks. Cost is tracked via the
    router's logging path.
    """
    router = _get_router()
    provider = router.get_provider(provider_name)
    if provider is None or not provider.is_configured():
        return "DOWN", "missing_key"

    request = LLMRequest(
        model=model,
        messages=[{"role": "user", "content": "Hi"}],
        temperature=0,
        max_tokens=5,
        purpose="doctor",
        trace_id="doctor",
        essential=True,
        project_id="openclaw",
        action="doctor",
    )
    try:
        resp = provider.generate_text(request)
        if resp and getattr(resp, "content", ""):
            _log_call(DOCTOR_BRAIN, resp.provider, resp.model, True, None, resp.usage, "doctor")
            return "OK", None
        _log_call(DOCTOR_BRAIN, provider_name, model, True, "empty_response", None, "doctor")
        return "DEGRADED", None
    except RuntimeError as exc:
        cls = _classify_transient(exc)
        _log_call(DOCTOR_BRAIN, provider_name, model, False, cls, None, "doctor")
        if cls == "non_transient":
            return "DOWN", "non_transient"
        return "DEGRADED", cls
    except Exception:
        _log_call(DOCTOR_BRAIN, provider_name, model, False, "non_transient", None, "doctor")
        return "DOWN", "non_transient"


def get_router() -> ModelRouter:
    """Get the underlying ``ModelRouter`` instance.

    Exposed for call sites that need direct access to router internals
    (e.g., budget config, provider status).
    """
    return _get_router()
