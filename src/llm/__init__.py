"""LLM Provider abstraction + fail-closed router for OpenClaw.

Public API (preferred — use llm_router for all new code):
  llm_router.generate(role, messages, ...)   -- central entrypoint for all LLM calls
  llm_router.resolve_provider_model(role)    -- resolve provider/model for a role
  llm_router.check_provider_health(p, m)     -- health check for doctor
  llm_router.get_router()                    -- underlying ModelRouter

Logical roles (from llm_router):
  CORE_BRAIN    -- general Q&A, planning
  REVIEW_BRAIN  -- code/diff review (hard-pinned to OpenAI, fail-closed)
  DOCTOR_BRAIN  -- health checks / diagnostics
  FAST_HELPER   -- cheap/fast model for simple tasks

Review gate invariant:
  REVIEW_BRAIN ALWAYS resolves to OpenAIProvider with CODEX_REVIEW_MODEL (primary).
  If OpenAI returns transient error (quota/rate/5xx/timeout), falls back to
  MistralProvider with configured fallback model.
  Both fail => fail-closed (ReviewFailClosedError).
  Missing OpenAI key => fail-closed (no silent fallback).

Budget enforcement:
  maxUsdPerReview cap checked before each review call (fail-closed).
  Cost telemetry written to artifacts.
"""

from src.llm.config import load_llm_config, validate_llm_config
from src.llm.router import ModelRouter, get_router
from src.llm.types import LLMConfig, ProviderConfig, PurposeRoute, LLMResponse
from src.llm.provider import BaseProvider
from src.llm.openai_provider import OpenAIProvider
from src.llm.mistral_provider import MistralProvider
from src.llm.moonshot_provider import MoonshotProvider
from src.llm.ollama_provider import OllamaProvider
from src.llm.budget import BudgetConfig, estimate_cost, check_budget, actual_cost
from src.llm.llm_router import (
    generate,
    resolve_provider_model,
    check_provider_health,
    CORE_BRAIN,
    REVIEW_BRAIN,
    DOCTOR_BRAIN,
    FAST_HELPER,
    ALL_ROLES,
    LLMRouterError,
    ConfigError,
    AuthError,
    RateLimitError,
    TransientError,
)

__all__ = [
    "load_llm_config",
    "validate_llm_config",
    "ModelRouter",
    "get_router",
    "LLMConfig",
    "ProviderConfig",
    "PurposeRoute",
    "LLMResponse",
    "BaseProvider",
    "OpenAIProvider",
    "MistralProvider",
    "MoonshotProvider",
    "OllamaProvider",
    "BudgetConfig",
    "estimate_cost",
    "check_budget",
    "actual_cost",
    "generate",
    "resolve_provider_model",
    "check_provider_health",
    "CORE_BRAIN",
    "REVIEW_BRAIN",
    "DOCTOR_BRAIN",
    "FAST_HELPER",
    "ALL_ROLES",
    "LLMRouterError",
    "ConfigError",
    "AuthError",
    "RateLimitError",
    "TransientError",
]
