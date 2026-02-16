"""Base provider interface for LLM providers.

All providers must implement generate_text(). Vision is optional (stub raises).
Every provider call accepts a trace_id for logging correlation.
Secrets are NEVER logged — all log output uses redacted references only.
"""

from __future__ import annotations

import re
import sys
from abc import ABC, abstractmethod

from src.llm.types import LLMRequest, LLMResponse


# Patterns that must never appear in log output
_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),        # OpenAI keys
    re.compile(r"Bearer\s+[A-Za-z0-9_-]{20,}"),   # Authorization headers
    re.compile(r"MOONSHOT[_A-Z]*=\S+"),            # Moonshot env patterns
    re.compile(r"[A-Za-z0-9_-]{32,}"),             # Generic long tokens (only in explicit checks)
]


def redact_for_log(text: str) -> str:
    """Redact potential secrets from a string before logging.

    Replaces OpenAI-style keys (sk-...), Bearer tokens, and env-var-style
    key assignments with [REDACTED].
    """
    text = re.sub(r"sk-[A-Za-z0-9_-]{20,}", "[REDACTED]", text)
    text = re.sub(r"Bearer\s+[A-Za-z0-9_-]{20,}", "Bearer [REDACTED]", text)
    # Redact env-var-style key assignments (KEY_NAME=value)
    text = re.sub(r"(MOONSHOT_API_KEY|OPENAI_API_KEY|API_KEY)=\S+", r"\1=[REDACTED]", text)
    return text


def _log(msg: str, trace_id: str = "") -> None:
    """Log to stderr with optional trace ID. Never logs secrets."""
    prefix = f"[llm:{trace_id}] " if trace_id else "[llm] "
    safe_msg = redact_for_log(msg)
    print(f"{prefix}{safe_msg}", file=sys.stderr)


class BaseProvider(ABC):
    """Abstract base class for LLM providers.

    Subclasses must implement:
      - generate_text(request) -> LLMResponse
      - provider_name (property)
      - is_configured() -> bool

    Optional:
      - generate_vision(request) -> LLMResponse  (default: raises NotImplementedError)
      - health_check() -> bool
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the canonical name of this provider (e.g., 'openai')."""
        ...

    @abstractmethod
    def is_configured(self) -> bool:
        """Return True if the provider has valid configuration (key present, etc).

        Must NOT call external APIs. Must NOT log secrets.
        """
        ...

    @abstractmethod
    def generate_text(self, request: LLMRequest) -> LLMResponse:
        """Generate text from the provider.

        Raises RuntimeError on failure (fail-closed).
        """
        ...

    def generate_vision(self, request: LLMRequest) -> LLMResponse:
        """Generate from a vision-capable model. Default: not implemented."""
        raise NotImplementedError(
            f"{self.provider_name} does not support vision requests"
        )

    def health_check(self) -> bool:
        """Lightweight health check (config present, API base parseable).

        Default implementation checks is_configured(). Providers may override
        for more specific checks, but must NOT call external APIs in test mode.
        """
        return self.is_configured()

    def get_status(self) -> dict:
        """Return status dict for HQ endpoint. Never exposes secrets."""
        configured = False
        try:
            configured = self.is_configured()
        except Exception:
            pass

        return {
            "name": self.provider_name,
            "configured": configured,
            "status": "active" if configured else "inactive",
        }
