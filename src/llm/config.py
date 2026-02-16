"""LLM config loader + strict schema validation.

Loads config/llm.json at startup. Invalid config => fail-closed with clear error.
Validates:
  - Required fields present
  - No unknown fields (additionalProperties: false equivalent)
  - Provider names are from the known set
  - review purpose ALWAYS maps to openai (enforced regardless of config)
  - Ollama API base must be localhost-only
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from src.llm.types import LLMConfig

# Known provider names (fail-closed: unknown providers are rejected)
KNOWN_PROVIDERS = {"openai", "moonshot", "ollama"}

# Known purpose names
KNOWN_PURPOSES = {"general", "review", "vision"}

# Required top-level keys in config
REQUIRED_TOP_KEYS = {"enabledProviders", "defaults", "providers"}
ALLOWED_TOP_KEYS = {"$schema", "enabledProviders", "defaults", "providers"}

# Required keys in a purpose route
REQUIRED_ROUTE_KEYS = {"provider", "model"}
ALLOWED_ROUTE_KEYS = {"provider", "model"}

# Allowed keys in a provider config
ALLOWED_PROVIDER_KEYS = {"apiBase", "keyEnv", "keySource", "enabled"}


class LLMConfigError(Exception):
    """Raised on config validation failure. Fail-closed."""
    pass


def _config_path() -> Path:
    """Resolve config/llm.json from repo root."""
    # Try relative to this file first (src/llm/config.py -> ../../config/llm.json)
    repo_root = Path(__file__).resolve().parent.parent.parent
    return repo_root / "config" / "llm.json"


def validate_llm_config(data: dict[str, Any]) -> list[str]:
    """Validate LLM config dict. Returns list of errors (empty = valid).

    Strict validation:
      - No unknown top-level keys
      - All required fields present
      - Provider names must be in KNOWN_PROVIDERS
      - Purpose names must be in KNOWN_PURPOSES
      - review purpose must map to openai provider
      - Ollama apiBase must be localhost-only
      - No empty strings where values are required
    """
    errors: list[str] = []

    # Top-level key validation
    top_keys = set(data.keys())
    unknown_top = top_keys - ALLOWED_TOP_KEYS
    if unknown_top:
        errors.append(f"Unknown top-level keys: {unknown_top}")
    missing_top = REQUIRED_TOP_KEYS - top_keys
    if missing_top:
        errors.append(f"Missing required top-level keys: {missing_top}")

    # enabledProviders validation
    enabled = data.get("enabledProviders")
    if not isinstance(enabled, list):
        errors.append("enabledProviders must be an array")
    elif enabled:
        for p in enabled:
            if p not in KNOWN_PROVIDERS:
                errors.append(f"Unknown provider in enabledProviders: '{p}'")
        if "openai" not in enabled:
            errors.append(
                "enabledProviders MUST include 'openai' (review gate requires it)"
            )

    # defaults validation
    defaults = data.get("defaults")
    if not isinstance(defaults, dict):
        errors.append("defaults must be an object")
    else:
        for purpose, route in defaults.items():
            if purpose not in KNOWN_PURPOSES:
                errors.append(f"Unknown purpose in defaults: '{purpose}'")
            if not isinstance(route, dict):
                errors.append(f"defaults.{purpose} must be an object")
                continue
            route_keys = set(route.keys())
            unknown_route = route_keys - ALLOWED_ROUTE_KEYS
            if unknown_route:
                errors.append(
                    f"Unknown keys in defaults.{purpose}: {unknown_route}"
                )
            missing_route = REQUIRED_ROUTE_KEYS - route_keys
            if missing_route:
                errors.append(
                    f"Missing required keys in defaults.{purpose}: {missing_route}"
                )
            if route.get("provider") not in KNOWN_PROVIDERS:
                errors.append(
                    f"Unknown provider in defaults.{purpose}: '{route.get('provider')}'"
                )
            if not route.get("model"):
                errors.append(f"defaults.{purpose}.model must be non-empty")

        # HARD INVARIANT: review must map to openai
        review_route = defaults.get("review", {})
        if isinstance(review_route, dict) and review_route.get("provider") != "openai":
            errors.append(
                "defaults.review.provider MUST be 'openai' "
                "(review gate is always OpenAI Codex, fail-closed)"
            )

    # providers validation
    providers = data.get("providers")
    if not isinstance(providers, dict):
        errors.append("providers must be an object")
    else:
        for pname, pconfig in providers.items():
            if pname not in KNOWN_PROVIDERS:
                errors.append(f"Unknown provider name: '{pname}'")
            if not isinstance(pconfig, dict):
                errors.append(f"providers.{pname} must be an object")
                continue
            pkeys = set(pconfig.keys())
            unknown_pkeys = pkeys - ALLOWED_PROVIDER_KEYS
            if unknown_pkeys:
                errors.append(
                    f"Unknown keys in providers.{pname}: {unknown_pkeys}"
                )
            # Ollama localhost-only check
            if pname == "ollama":
                api_base = pconfig.get("apiBase", "")
                if api_base and not _is_localhost(api_base):
                    errors.append(
                        f"providers.ollama.apiBase must be localhost "
                        f"(127.0.0.1/::1/localhost), got: '{api_base}'"
                    )

    return errors


def _is_localhost(url: str) -> bool:
    """Check if URL points to localhost."""
    lower = url.lower()
    return "127.0.0.1" in lower or "localhost" in lower or "[::1]" in lower


def load_llm_config(config_path: str | Path | None = None) -> LLMConfig:
    """Load and validate config/llm.json. Fail-closed on any error.

    Parameters
    ----------
    config_path : str | Path | None
        Override path to config file. None -> auto-resolve from repo root.

    Returns
    -------
    LLMConfig
        Validated configuration.

    Raises
    ------
    LLMConfigError
        If config file is missing, unparseable, or fails validation.
    """
    path = Path(config_path) if config_path else _config_path()

    if not path.is_file():
        raise LLMConfigError(
            f"LLM config file not found: {path}\n"
            "Create config/llm.json or copy from config/llm.json.example"
        )

    try:
        with open(path) as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        raise LLMConfigError(
            f"LLM config file is not valid JSON: {path}\n{exc}"
        ) from None

    if not isinstance(data, dict):
        raise LLMConfigError(
            f"LLM config must be a JSON object, got: {type(data).__name__}"
        )

    errors = validate_llm_config(data)
    if errors:
        error_msg = "LLM config validation failed (fail-closed):\n"
        for err in errors:
            error_msg += f"  - {err}\n"
        error_msg += f"\nConfig file: {path}"
        raise LLMConfigError(error_msg)

    return LLMConfig.from_dict(data)
