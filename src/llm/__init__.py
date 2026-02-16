"""LLM Provider abstraction + fail-closed router for OpenClaw.

Public API:
  load_llm_config()       -> LLMConfig         -- load + validate config/llm.json
  get_router()            -> ModelRouter        -- singleton router from config
  get_provider(purpose)   -> BaseProvider       -- resolved provider for a purpose

Review gate invariant:
  purpose="review" ALWAYS resolves to OpenAIProvider with CODEX_REVIEW_MODEL (primary).
  If OpenAI returns transient error (quota/rate/5xx/timeout), falls back to
  MistralProvider with codestral-2501.
  Both fail => fail-closed (RuntimeError).
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
]
