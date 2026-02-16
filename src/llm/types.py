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
class LLMConfig:
    """Top-level LLM configuration (loaded from config/llm.json)."""

    enabled_providers: list[str] = field(default_factory=lambda: ["openai"])
    defaults: dict[str, PurposeRoute] = field(default_factory=dict)
    providers: dict[str, ProviderConfig] = field(default_factory=dict)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "LLMConfig":
        defaults = {}
        for purpose, route_data in data.get("defaults", {}).items():
            defaults[purpose] = PurposeRoute.from_dict(route_data)

        providers = {}
        for pname, pdata in data.get("providers", {}).items():
            providers[pname] = ProviderConfig.from_dict(pname, pdata)

        return LLMConfig(
            enabled_providers=data.get("enabledProviders", ["openai"]),
            defaults=defaults,
            providers=providers,
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


@dataclass
class LLMResponse:
    """Response from an LLM provider."""

    content: str
    model: str
    provider: str
    usage: dict[str, int] = field(default_factory=dict)
    trace_id: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


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
