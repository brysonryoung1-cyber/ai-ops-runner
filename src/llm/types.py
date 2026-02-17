"""Type definitions for LLM provider abstraction.

All types are plain dataclasses — no external dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ProviderConfig:
    """Configuration for a single LLM provider."""

    name: str
    enabled: bool = False
    api_base: str = ""
    key_env: str = ""
    key_source: str = ""

    @staticmethod
    def from_dict(name: str, data: dict[str, Any]) -> "ProviderConfig":
        return ProviderConfig(
            name=name,
            enabled=data.get("enabled", False),
            api_base=data.get("apiBase", ""),
            key_env=data.get("keyEnv", ""),
            key_source=data.get("keySource", ""),
        )


@dataclass
class PurposeRoute:
    """Routing rule: purpose -> provider + model."""

    provider: str
    model: str

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "PurposeRoute":
        return PurposeRoute(
            provider=data["provider"],
            model=data["model"],
        )


@dataclass
class ReviewFallbackConfig:
    """Fallback reviewer config (used when OpenAI returns quota/rate/5xx)."""

    provider: str = ""
    model: str = ""

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "ReviewFallbackConfig":
        return ReviewFallbackConfig(
            provider=data.get("provider", ""),
            model=data.get("model", ""),
        )


@dataclass
class ReviewCapsConfig:
    """Strict caps for review gate API calls."""

    max_output_tokens: int = 600
    temperature: float = 0.0

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "ReviewCapsConfig":
        return ReviewCapsConfig(
            max_output_tokens=data.get("maxOutputTokens", 600),
            temperature=data.get("temperature", 0.0),
        )


@dataclass
class LLMConfig:
    """Top-level LLM configuration (loaded from config/llm.json)."""

    enabled_providers: list[str] = field(default_factory=lambda: ["openai"])
    defaults: dict[str, PurposeRoute] = field(default_factory=dict)
    providers: dict[str, ProviderConfig] = field(default_factory=dict)
    review_fallback: ReviewFallbackConfig | None = None
    review_caps: ReviewCapsConfig = field(default_factory=ReviewCapsConfig)
    budget_config: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "LLMConfig":
        defaults = {}
        for purpose, route_data in data.get("defaults", {}).items():
            defaults[purpose] = PurposeRoute.from_dict(route_data)

        providers = {}
        for pname, pdata in data.get("providers", {}).items():
            providers[pname] = ProviderConfig.from_dict(pname, pdata)

        review_fallback = None
        if "reviewFallback" in data:
            review_fallback = ReviewFallbackConfig.from_dict(data["reviewFallback"])

        review_caps = ReviewCapsConfig()
        if "reviewCaps" in data:
            review_caps = ReviewCapsConfig.from_dict(data["reviewCaps"])

        return LLMConfig(
            enabled_providers=data.get("enabledProviders", ["openai"]),
            defaults=defaults,
            providers=providers,
            review_fallback=review_fallback,
            review_caps=review_caps,
            budget_config=data.get("budget", {}),
        )


@dataclass
class LLMRequest:
    """Request to an LLM provider."""

    model: str
    messages: list[dict[str, str]]
    temperature: float = 0.0
    max_tokens: int | None = None
    purpose: str = "general"
    trace_id: str = ""
    response_format: dict[str, str] | None = None
    """When True, cost guard is skipped (doctor/deploy/guard)."""
    essential: bool = False


@dataclass
class LLMResponse:
    """Response from an LLM provider."""

    content: str
    model: str
    provider: str
    usage: dict[str, int] = field(default_factory=dict)
    trace_id: str = ""
    raw: dict[str, Any] = field(default_factory=dict)
    # Set when review used fallback (primary failed with this transient class)
    primary_transient_class: str | None = None


class ReviewFailClosedError(RuntimeError):
    """Raised when both primary and fallback reviewers fail (fail-closed).

    Carries structured error info for artifact writing; never exposes secrets.
    """

    def __init__(
        self,
        message: str,
        primary_error: str,
        fallback_error: str,
        primary_transient_class: str,
    ):
        super().__init__(message)
        self.primary_error = primary_error
        self.fallback_error = fallback_error
        self.primary_transient_class = primary_transient_class


@dataclass
class ProviderStatus:
    """Status of a single LLM provider (for HQ status endpoint)."""

    name: str
    enabled: bool
    configured: bool
    status: str  # "active" | "inactive" | "disabled" | "error"
    fingerprint: str | None = None
    last_check: str | None = None
    error: str | None = None
