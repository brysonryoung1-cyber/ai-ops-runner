"""LLM config loader + strict schema validation.

Loads config/llm.json at startup. Invalid config => fail-closed with clear error.
Validates:
  - Required fields present
  - No unknown fields (additionalProperties: false equivalent)
  - Provider names are from the known set
  - review purpose ALWAYS maps to openai (enforced regardless of config)
  - Ollama API base must be localhost-only
  - reviewFallback provider must not be openai
  - budget caps must be positive
  - reviewCaps must have sane bounds
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from src.llm.types import LLMConfig

# Known provider names (fail-closed: unknown providers are rejected)
KNOWN_PROVIDERS = {"openai", "mistral", "moonshot", "ollama"}

# Known purpose names
KNOWN_PURPOSES = {"general", "review", "vision"}

# Required top-level keys in config
REQUIRED_TOP_KEYS = {"enabledProviders", "defaults", "providers"}
ALLOWED_TOP_KEYS = {
    "$schema", "enabledProviders", "defaults", "providers",
    "reviewFallback", "budget", "reviewCaps",
}

# Required keys in a purpose route
REQUIRED_ROUTE_KEYS = {"provider", "model"}
ALLOWED_ROUTE_KEYS = {"provider", "model"}

# Allowed keys in a provider config
ALLOWED_PROVIDER_KEYS = {"apiBase", "keyEnv", "keySource", "enabled"}

# Allowed keys in reviewFallback
ALLOWED_REVIEW_FALLBACK_KEYS = {"provider", "model"}

# Allowed keys in budget
ALLOWED_BUDGET_KEYS = {"maxUsdPerReview", "maxUsdPerRun", "pricing"}

# Allowed keys in reviewCaps
ALLOWED_REVIEW_CAPS_KEYS = {"maxOutputTokens", "temperature"}


class LLMConfigError(Exception):
    """Raised on config validation failure. Fail-closed."""
    pass


def _config_path() -> Path:
    """Resolve config/llm.json from repo root."""
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
      - reviewFallback provider must NOT be openai
      - budget caps must be positive
      - reviewCaps.maxOutputTokens must be 100–4096
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
                "(review gate primary is always OpenAI, fail-closed)"
            )

    # reviewFallback validation (optional)
    review_fallback = data.get("reviewFallback")
    if review_fallback is not None:
        if not isinstance(review_fallback, dict):
            errors.append("reviewFallback must be an object")
        else:
            rf_keys = set(review_fallback.keys())
            unknown_rf = rf_keys - ALLOWED_REVIEW_FALLBACK_KEYS
            if unknown_rf:
                errors.append(f"Unknown keys in reviewFallback: {unknown_rf}")
            if not review_fallback.get("provider"):
                errors.append("reviewFallback.provider must be non-empty")
            elif review_fallback["provider"] == "openai":
                errors.append(
                    "reviewFallback.provider must NOT be 'openai' "
                    "(that's the primary — fallback must be a different vendor)"
                )
            elif review_fallback["provider"] not in KNOWN_PROVIDERS:
                errors.append(
                    f"Unknown provider in reviewFallback: "
                    f"'{review_fallback['provider']}'"
                )
            if not review_fallback.get("model"):
                errors.append("reviewFallback.model must be non-empty")

    # budget validation (optional)
    budget = data.get("budget")
    if budget is not None:
        if not isinstance(budget, dict):
            errors.append("budget must be an object")
        else:
            budget_keys = set(budget.keys())
            unknown_budget = budget_keys - ALLOWED_BUDGET_KEYS
            if unknown_budget:
                errors.append(f"Unknown keys in budget: {unknown_budget}")
            for cap_key in ("maxUsdPerReview", "maxUsdPerRun"):
                val = budget.get(cap_key)
                if val is not None:
                    if not isinstance(val, (int, float)) or val <= 0:
                        errors.append(
                            f"budget.{cap_key} must be a positive number"
                        )
            pricing = budget.get("pricing")
            if pricing is not None:
                if not isinstance(pricing, dict):
                    errors.append("budget.pricing must be an object")
                else:
                    for model_name, price_data in pricing.items():
                        if not isinstance(price_data, dict):
                            errors.append(
                                f"budget.pricing.{model_name} must be an object"
                            )
                            continue
                        for price_key in ("inputPer1M", "outputPer1M"):
                            pv = price_data.get(price_key)
                            if pv is not None and (
                                not isinstance(pv, (int, float)) or pv < 0
                            ):
                                errors.append(
                                    f"budget.pricing.{model_name}.{price_key} "
                                    f"must be a non-negative number"
                                )

    # reviewCaps validation (optional)
    review_caps = data.get("reviewCaps")
    if review_caps is not None:
        if not isinstance(review_caps, dict):
            errors.append("reviewCaps must be an object")
        else:
            rc_keys = set(review_caps.keys())
            unknown_rc = rc_keys - ALLOWED_REVIEW_CAPS_KEYS
            if unknown_rc:
                errors.append(f"Unknown keys in reviewCaps: {unknown_rc}")
            mot = review_caps.get("maxOutputTokens")
            if mot is not None:
                if not isinstance(mot, int) or mot < 100 or mot > 4096:
                    errors.append(
                        "reviewCaps.maxOutputTokens must be an integer 100–4096"
                    )
            temp = review_caps.get("temperature")
            if temp is not None:
                if not isinstance(temp, (int, float)) or temp < 0 or temp > 1:
                    errors.append(
                        "reviewCaps.temperature must be a number 0–1"
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
