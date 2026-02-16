"""LLM Provider abstraction + fail-closed router for OpenClaw.

Public API:
  load_llm_config()       -> LLMConfig         -- load + validate config/llm.json
  get_router()            -> ModelRouter        -- singleton router from config
  get_provider(purpose)   -> BaseProvider       -- resolved provider for a purpose

Review gate invariant:
  purpose="review" ALWAYS resolves to OpenAIProvider with CODEX_REVIEW_MODEL.
  No fallback, no override. Missing OpenAI key => fail-closed (RuntimeError).
"""

from src.llm.config import load_llm_config, validate_llm_config
from src.llm.router import ModelRouter, get_router
from src.llm.types import LLMConfig, ProviderConfig, PurposeRoute, LLMResponse
from src.llm.provider import BaseProvider
from src.llm.openai_provider import OpenAIProvider
from src.llm.moonshot_provider import MoonshotProvider
from src.llm.ollama_provider import OllamaProvider

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
    "MoonshotProvider",
    "OllamaProvider",
]
