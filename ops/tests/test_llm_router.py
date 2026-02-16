"""Hermetic tests for LLM provider abstraction + fail-closed router.

Tests verify:
  1. purpose=review always selects OpenAI + CODEX_REVIEW_MODEL
     even if defaults.general is moonshot/ollama
  2. Missing OpenAI key => review invocation fails closed (RuntimeError)
  3. Config schema rejects unknown fields and missing required fields
  4. Secret scan: logs/artifacts never contain key-shaped strings
  5. Config schema validation: good config passes, bad config fails
  6. Provider instantiation and status (mocked — no real API calls)
  7. Ollama localhost-only enforcement
  8. Router fallback behavior for non-review purposes
  9. Review uses gpt-4o-mini by default
  10. max_output_tokens enforced on review calls
  11. OpenAI quota error triggers Codestral fallback
  12. Both reviewers fail => fail-closed
  13. Budget cap blocks oversized call
  14. Provenance recorded (primary + fallback)
  15. Mistral provider instantiation and status
  16. Config validates reviewFallback, budget, reviewCaps

All tests are HERMETIC — no network calls, no real secrets, no side effects.
"""

import importlib
import json
import os
import re
import sys
import io
from pathlib import Path
from unittest import mock

import pytest

# Ensure repo root is on path
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.llm.types import (
    LLMConfig, PurposeRoute, ProviderConfig, LLMRequest, LLMResponse,
    ReviewFallbackConfig, ReviewCapsConfig,
)
from src.llm.config import validate_llm_config, load_llm_config, LLMConfigError
from src.llm.router import ModelRouter, reset_router, _is_transient_error
from src.llm.provider import BaseProvider, redact_for_log
from src.llm.openai_provider import OpenAIProvider, CODEX_REVIEW_MODEL, _mask_key
from src.llm.mistral_provider import MistralProvider
from src.llm.moonshot_provider import MoonshotProvider
from src.llm.ollama_provider import OllamaProvider
from src.llm.budget import (
    BudgetConfig, estimate_cost, check_budget, actual_cost,
    DEFAULT_MAX_USD_PER_REVIEW,
)

FAKE_OPENAI_KEY = "sk-test-FAKE-000000000000000000000000000000000000"
FAKE_MOONSHOT_KEY = "msk-test-FAKE-0000000000000000000000000000000000"
FAKE_MISTRAL_KEY = "mist-test-FAKE-00000000000000000000000000000000"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Reset the router singleton before each test."""
    reset_router()
    yield
    reset_router()


def _good_config() -> dict:
    """Return a valid config dict for testing."""
    return {
        "enabledProviders": ["openai"],
        "defaults": {
            "general": {"provider": "openai", "model": "gpt-4o-mini"},
            "review": {"provider": "openai", "model": "gpt-4o-mini"},
        },
        "reviewFallback": {
            "provider": "mistral",
            "model": "codestral-2501",
        },
        "budget": {
            "maxUsdPerReview": 0.50,
            "maxUsdPerRun": 5.00,
            "pricing": {
                "gpt-4o-mini": {"inputPer1M": 1.50, "outputPer1M": 6.00},
                "gpt-4o": {"inputPer1M": 2.50, "outputPer1M": 10.00},
                "codestral-2501": {"inputPer1M": 0.30, "outputPer1M": 0.90},
            },
        },
        "reviewCaps": {
            "maxOutputTokens": 600,
            "temperature": 0,
        },
        "providers": {
            "openai": {
                "apiBase": "https://api.openai.com/v1",
                "keySource": "existing_secret_store",
            },
            "mistral": {
                "apiBase": "https://api.mistral.ai/v1",
                "keyEnv": "MISTRAL_API_KEY",
                "enabled": False,
            },
            "moonshot": {
                "apiBase": "https://api.moonshot.cn/v1",
                "keyEnv": "MOONSHOT_API_KEY",
                "enabled": False,
            },
            "ollama": {
                "apiBase": "http://127.0.0.1:11434",
                "enabled": False,
            },
        },
    }


def _make_config(overrides: dict | None = None) -> LLMConfig:
    """Create an LLMConfig from the good config with optional overrides."""
    data = _good_config()
    if overrides:
        data.update(overrides)
    return LLMConfig.from_dict(data)


def _fake_openai_response(content: str = '{"verdict":"APPROVED","blockers":[],"non_blocking":[]}') -> LLMResponse:
    """Create a fake LLMResponse as if from OpenAI."""
    return LLMResponse(
        content=content,
        model="gpt-4o-mini",
        provider="openai",
        usage={"prompt_tokens": 500, "completion_tokens": 100, "total_tokens": 600},
        trace_id="test",
    )


def _fake_mistral_response(content: str = '{"verdict":"APPROVED","blockers":[],"non_blocking":[]}') -> LLMResponse:
    """Create a fake LLMResponse as if from Mistral."""
    return LLMResponse(
        content=content,
        model="codestral-2501",
        provider="mistral",
        usage={"prompt_tokens": 500, "completion_tokens": 100, "total_tokens": 600},
        trace_id="test",
    )


# ===========================================================================
# 1. Review always selects OpenAI + CODEX_REVIEW_MODEL
# ===========================================================================


class TestReviewPinning:
    """purpose=review ALWAYS resolves to OpenAI, regardless of config."""

    def test_review_selects_openai_default_config(self):
        config = _make_config()
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": FAKE_OPENAI_KEY}):
            router = ModelRouter(config=config)
            provider, model = router.resolve("review")
        assert provider.provider_name == "openai"
        assert model == CODEX_REVIEW_MODEL

    def test_review_selects_openai_even_if_general_is_moonshot(self):
        """Even when general purpose routes to moonshot, review stays OpenAI."""
        config = _make_config({
            "enabledProviders": ["openai", "moonshot"],
            "defaults": {
                "general": {"provider": "moonshot", "model": "moonshot-v1-8k"},
                "review": {"provider": "openai", "model": "gpt-4o-mini"},
            },
        })
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": FAKE_OPENAI_KEY}):
            router = ModelRouter(config=config)
            provider, model = router.resolve("review")
        assert provider.provider_name == "openai"
        assert model == CODEX_REVIEW_MODEL

    def test_review_selects_openai_even_if_general_is_ollama(self):
        """Even when general purpose routes to ollama, review stays OpenAI."""
        config = _make_config({
            "enabledProviders": ["openai", "ollama"],
            "defaults": {
                "general": {"provider": "ollama", "model": "llama3"},
                "review": {"provider": "openai", "model": "gpt-4o-mini"},
            },
        })
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": FAKE_OPENAI_KEY}):
            router = ModelRouter(config=config)
            provider, model = router.resolve("review")
        assert provider.provider_name == "openai"
        assert model == CODEX_REVIEW_MODEL

    def test_review_model_comes_from_env(self):
        """OPENCLAW_REVIEW_MODEL env var controls the review model."""
        with mock.patch.dict(os.environ, {
            "OPENAI_API_KEY": FAKE_OPENAI_KEY,
            "OPENCLAW_REVIEW_MODEL": "gpt-5.3-codex",
        }):
            import src.llm.openai_provider as oai_mod
            importlib.reload(oai_mod)
            assert oai_mod.CODEX_REVIEW_MODEL == "gpt-5.3-codex"
        # Restore default
        with mock.patch.dict(os.environ, {"OPENCLAW_REVIEW_MODEL": "gpt-4o-mini"}):
            importlib.reload(oai_mod)

    def test_review_default_model_is_4o_mini(self):
        """Default review model should be gpt-4o-mini (cost-optimized)."""
        with mock.patch.dict(os.environ, {}, clear=False):
            # Remove any override
            env = dict(os.environ)
            env.pop("OPENCLAW_REVIEW_MODEL", None)
            with mock.patch.dict(os.environ, env, clear=True):
                import src.llm.openai_provider as oai_mod
                importlib.reload(oai_mod)
                assert oai_mod.CODEX_REVIEW_MODEL == "gpt-4o-mini"
        # Restore
        with mock.patch.dict(os.environ, {"OPENCLAW_REVIEW_MODEL": "gpt-4o-mini"}):
            importlib.reload(oai_mod)


# ===========================================================================
# 2. Missing OpenAI key => review fails closed
# ===========================================================================


class TestReviewFailClosed:
    """Missing OpenAI key must cause review to fail with clear error."""

    def test_review_fails_without_openai_key(self):
        config = _make_config()
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch(
                "src.llm.openai_provider._load_openai_key", return_value=None
            ):
                router = ModelRouter(config=config)
                with pytest.raises(RuntimeError, match="OpenAI API key not found"):
                    router.resolve("review")

    def test_review_fails_with_empty_key(self):
        config = _make_config()
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            with mock.patch(
                "src.llm.openai_provider._load_openai_key", return_value=None
            ):
                router = ModelRouter(config=config)
                with pytest.raises(RuntimeError, match="OpenAI API key not found"):
                    router.resolve("review")

    def test_review_error_is_clear(self):
        """Error message must include actionable instructions."""
        config = _make_config()
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch(
                "src.llm.openai_provider._load_openai_key", return_value=None
            ):
                router = ModelRouter(config=config)
                try:
                    router.resolve("review")
                    assert False, "Should have raised"
                except RuntimeError as exc:
                    msg = str(exc)
                    assert "fail-closed" in msg.lower() or "FATAL" in msg
                    assert "openai_key.py" in msg or "OPENAI_API_KEY" in msg


# ===========================================================================
# 3. Config schema validation
# ===========================================================================


class TestConfigValidation:
    """Config schema validation: good config passes, bad config fails."""

    def test_good_config_passes(self):
        errors = validate_llm_config(_good_config())
        assert errors == []

    def test_missing_enabled_providers(self):
        data = _good_config()
        del data["enabledProviders"]
        errors = validate_llm_config(data)
        assert any("enabledProviders" in e for e in errors)

    def test_missing_defaults(self):
        data = _good_config()
        del data["defaults"]
        errors = validate_llm_config(data)
        assert any("defaults" in e for e in errors)

    def test_missing_providers(self):
        data = _good_config()
        del data["providers"]
        errors = validate_llm_config(data)
        assert any("providers" in e for e in errors)

    def test_unknown_top_level_key_rejected(self):
        data = _good_config()
        data["unknownField"] = "bad"
        errors = validate_llm_config(data)
        assert any("unknownField" in str(e) for e in errors)

    def test_unknown_provider_rejected(self):
        data = _good_config()
        data["enabledProviders"] = ["openai", "unknown_provider"]
        errors = validate_llm_config(data)
        assert any("unknown_provider" in e for e in errors)

    def test_openai_required_in_enabled(self):
        data = _good_config()
        data["enabledProviders"] = ["moonshot"]
        errors = validate_llm_config(data)
        assert any("openai" in e.lower() for e in errors)

    def test_unknown_purpose_rejected(self):
        data = _good_config()
        data["defaults"]["unknown_purpose"] = {"provider": "openai", "model": "x"}
        errors = validate_llm_config(data)
        assert any("unknown_purpose" in e for e in errors)

    def test_review_must_be_openai(self):
        data = _good_config()
        data["defaults"]["review"] = {"provider": "moonshot", "model": "x"}
        errors = validate_llm_config(data)
        assert any("review" in e and "openai" in e.lower() for e in errors)

    def test_unknown_provider_key_rejected(self):
        data = _good_config()
        data["providers"]["openai"]["badKey"] = "value"
        errors = validate_llm_config(data)
        assert any("badKey" in str(e) for e in errors)

    def test_unknown_route_key_rejected(self):
        data = _good_config()
        data["defaults"]["general"]["extraField"] = "bad"
        errors = validate_llm_config(data)
        assert any("extraField" in str(e) for e in errors)

    def test_empty_model_rejected(self):
        data = _good_config()
        data["defaults"]["general"]["model"] = ""
        errors = validate_llm_config(data)
        assert any("model" in e and "non-empty" in e for e in errors)

    def test_ollama_non_localhost_rejected(self):
        data = _good_config()
        data["providers"]["ollama"]["apiBase"] = "https://public.example.com:11434"
        errors = validate_llm_config(data)
        assert any("localhost" in e for e in errors)

    def test_ollama_localhost_accepted(self):
        data = _good_config()
        data["providers"]["ollama"]["apiBase"] = "http://127.0.0.1:11434"
        errors = validate_llm_config(data)
        assert not any("localhost" in e for e in errors)

    # --- reviewFallback validation ---

    def test_review_fallback_openai_rejected(self):
        """reviewFallback.provider must NOT be openai."""
        data = _good_config()
        data["reviewFallback"] = {"provider": "openai", "model": "gpt-4o"}
        errors = validate_llm_config(data)
        assert any("reviewFallback" in e and "openai" in e.lower() for e in errors)

    def test_review_fallback_valid_mistral(self):
        """Mistral as review fallback should pass validation."""
        data = _good_config()
        data["reviewFallback"] = {"provider": "mistral", "model": "codestral-2501"}
        errors = validate_llm_config(data)
        assert not any("reviewFallback" in e for e in errors)

    def test_review_fallback_empty_model_rejected(self):
        data = _good_config()
        data["reviewFallback"] = {"provider": "mistral", "model": ""}
        errors = validate_llm_config(data)
        assert any("reviewFallback" in e and "model" in e for e in errors)

    def test_review_fallback_unknown_provider_rejected(self):
        data = _good_config()
        data["reviewFallback"] = {"provider": "unknown_vendor", "model": "x"}
        errors = validate_llm_config(data)
        assert any("reviewFallback" in e and "unknown_vendor" in e for e in errors)

    # --- budget validation ---

    def test_budget_valid(self):
        data = _good_config()
        errors = validate_llm_config(data)
        assert not any("budget" in e for e in errors)

    def test_budget_negative_cap_rejected(self):
        data = _good_config()
        data["budget"]["maxUsdPerReview"] = -1.0
        errors = validate_llm_config(data)
        assert any("maxUsdPerReview" in e and "positive" in e for e in errors)

    def test_budget_zero_cap_rejected(self):
        data = _good_config()
        data["budget"]["maxUsdPerRun"] = 0
        errors = validate_llm_config(data)
        assert any("maxUsdPerRun" in e and "positive" in e for e in errors)

    def test_budget_negative_pricing_rejected(self):
        data = _good_config()
        data["budget"]["pricing"]["gpt-4o"]["inputPer1M"] = -5
        errors = validate_llm_config(data)
        assert any("inputPer1M" in e and "non-negative" in e for e in errors)

    # --- reviewCaps validation ---

    def test_review_caps_valid(self):
        data = _good_config()
        errors = validate_llm_config(data)
        assert not any("reviewCaps" in e for e in errors)

    def test_review_caps_too_low_rejected(self):
        data = _good_config()
        data["reviewCaps"]["maxOutputTokens"] = 50
        errors = validate_llm_config(data)
        assert any("maxOutputTokens" in e and "100" in e for e in errors)

    def test_review_caps_too_high_rejected(self):
        data = _good_config()
        data["reviewCaps"]["maxOutputTokens"] = 10000
        errors = validate_llm_config(data)
        assert any("maxOutputTokens" in e and "4096" in e for e in errors)

    def test_review_caps_bad_temperature(self):
        data = _good_config()
        data["reviewCaps"]["temperature"] = 2.0
        errors = validate_llm_config(data)
        assert any("temperature" in e for e in errors)

    # --- mistral provider in config ---

    def test_mistral_in_enabled_providers(self):
        data = _good_config()
        data["enabledProviders"] = ["openai", "mistral"]
        errors = validate_llm_config(data)
        assert not any("mistral" in e for e in errors)


class TestConfigLoading:
    """Test config file loading (with temp files)."""

    def test_load_good_config(self, tmp_path):
        config_file = tmp_path / "llm.json"
        config_file.write_text(json.dumps(_good_config()))
        config = load_llm_config(config_file)
        assert "openai" in config.enabled_providers
        assert config.defaults["review"].provider == "openai"

    def test_load_missing_file_fails(self, tmp_path):
        with pytest.raises(LLMConfigError, match="not found"):
            load_llm_config(tmp_path / "nonexistent.json")

    def test_load_invalid_json_fails(self, tmp_path):
        config_file = tmp_path / "llm.json"
        config_file.write_text("{ not valid json }")
        with pytest.raises(LLMConfigError, match="not valid JSON"):
            load_llm_config(config_file)

    def test_load_invalid_schema_fails(self, tmp_path):
        bad_config = _good_config()
        bad_config["enabledProviders"] = ["moonshot"]  # Missing openai
        config_file = tmp_path / "llm.json"
        config_file.write_text(json.dumps(bad_config))
        with pytest.raises(LLMConfigError, match="validation failed"):
            load_llm_config(config_file)

    def test_load_actual_repo_config(self):
        """The real config/llm.json in the repo should be valid."""
        repo_config = REPO_ROOT / "config" / "llm.json"
        if repo_config.is_file():
            config = load_llm_config(repo_config)
            assert "openai" in config.enabled_providers
            assert config.defaults["review"].provider == "openai"

    def test_load_config_with_review_fallback(self, tmp_path):
        """Config with reviewFallback should load correctly."""
        config_file = tmp_path / "llm.json"
        config_file.write_text(json.dumps(_good_config()))
        config = load_llm_config(config_file)
        assert config.review_fallback is not None
        assert config.review_fallback.provider == "mistral"
        assert config.review_fallback.model == "codestral-2501"

    def test_load_config_with_review_caps(self, tmp_path):
        """Config with reviewCaps should load correctly."""
        config_file = tmp_path / "llm.json"
        config_file.write_text(json.dumps(_good_config()))
        config = load_llm_config(config_file)
        assert config.review_caps.max_output_tokens == 600
        assert config.review_caps.temperature == 0

    def test_load_config_with_budget(self, tmp_path):
        """Config with budget should load correctly."""
        config_file = tmp_path / "llm.json"
        config_file.write_text(json.dumps(_good_config()))
        config = load_llm_config(config_file)
        assert config.budget_config.get("maxUsdPerReview") == 0.50
        assert config.budget_config.get("maxUsdPerRun") == 5.00


# ===========================================================================
# 4. Secret scan: no secrets in logs
# ===========================================================================


class TestSecretRedaction:
    """Verify secrets never appear in log output."""

    def test_redact_openai_key(self):
        text = f"Authorization: Bearer {FAKE_OPENAI_KEY}"
        redacted = redact_for_log(text)
        assert FAKE_OPENAI_KEY not in redacted
        assert "[REDACTED]" in redacted

    def test_redact_sk_prefix(self):
        text = f"key is sk-proj-1234567890abcdefghijklmnop"
        redacted = redact_for_log(text)
        assert "sk-proj-1234567890abcdefghijklmnop" not in redacted

    def test_redact_bearer_token(self):
        text = "Bearer sk-test-1234567890abcdefghij"
        redacted = redact_for_log(text)
        assert "sk-test-1234567890abcdefghij" not in redacted

    def test_safe_text_unchanged(self):
        text = "This is a normal log message with no secrets"
        assert redact_for_log(text) == text

    def test_mask_key_hides_openai_key(self):
        masked = _mask_key(FAKE_OPENAI_KEY)
        assert FAKE_OPENAI_KEY not in masked
        assert "…" in masked
        assert len(masked) < len(FAKE_OPENAI_KEY)

    def test_provider_status_never_exposes_key(self):
        """OpenAI provider status must mask the key."""
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": FAKE_OPENAI_KEY}):
            provider = OpenAIProvider()
            status = provider.get_status()
            status_str = json.dumps(status)
            assert FAKE_OPENAI_KEY not in status_str
            assert status["fingerprint"] is not None
            assert "…" in status["fingerprint"]

    def test_router_status_never_exposes_key(self):
        """Router get_all_status must never contain raw keys."""
        config = _make_config()
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": FAKE_OPENAI_KEY}):
            router = ModelRouter(config=config)
            statuses = router.get_all_status()
            all_text = json.dumps(statuses)
            assert FAKE_OPENAI_KEY not in all_text

    def test_moonshot_status_never_exposes_key(self):
        with mock.patch.dict(os.environ, {"MOONSHOT_API_KEY": FAKE_MOONSHOT_KEY}):
            provider = MoonshotProvider()
            status = provider.get_status()
            status_str = json.dumps(status)
            assert FAKE_MOONSHOT_KEY not in status_str

    def test_mistral_status_never_exposes_key(self):
        """Mistral provider status must mask the key."""
        with mock.patch.dict(os.environ, {"MISTRAL_API_KEY": FAKE_MISTRAL_KEY}):
            provider = MistralProvider()
            status = provider.get_status()
            status_str = json.dumps(status)
            assert FAKE_MISTRAL_KEY not in status_str
            assert status["fingerprint"] is not None
            assert "…" in status["fingerprint"]

    def test_error_messages_redacted(self):
        """Error messages from API failures must be redacted."""
        text = f"API error: Bearer {FAKE_OPENAI_KEY} was invalid"
        redacted = redact_for_log(text)
        assert FAKE_OPENAI_KEY not in redacted


# ===========================================================================
# 5. Provider instantiation
# ===========================================================================


class TestProviders:
    """Test provider instantiation and configuration checks."""

    def test_openai_configured_with_key(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": FAKE_OPENAI_KEY}):
            p = OpenAIProvider()
            assert p.is_configured()
            assert p.provider_name == "openai"

    def test_openai_not_configured_without_key(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch(
                "src.llm.openai_provider._load_openai_key", return_value=None
            ):
                p = OpenAIProvider()
                assert not p.is_configured()

    def test_mistral_configured_with_key(self):
        with mock.patch.dict(os.environ, {"MISTRAL_API_KEY": FAKE_MISTRAL_KEY}):
            p = MistralProvider()
            assert p.is_configured()
            assert p.provider_name == "mistral"

    def test_mistral_not_configured_without_key(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            p = MistralProvider()
            assert not p.is_configured()

    def test_moonshot_configured_with_key(self):
        with mock.patch.dict(os.environ, {"MOONSHOT_API_KEY": FAKE_MOONSHOT_KEY}):
            p = MoonshotProvider()
            assert p.is_configured()
            assert p.provider_name == "moonshot"

    def test_moonshot_not_configured_without_key(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            p = MoonshotProvider()
            assert not p.is_configured()

    def test_ollama_localhost_accepted(self):
        p = OllamaProvider(api_base="http://127.0.0.1:11434")
        assert p.provider_name == "ollama"

    def test_ollama_public_rejected(self):
        with pytest.raises(ValueError, match="localhost"):
            OllamaProvider(api_base="https://public.example.com:11434")

    def test_ollama_ipv6_localhost_accepted(self):
        p = OllamaProvider(api_base="http://[::1]:11434")
        assert p.provider_name == "ollama"

    def test_vision_not_implemented(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": FAKE_OPENAI_KEY}):
            p = OpenAIProvider()
            req = LLMRequest(model="gpt-4o", messages=[], purpose="vision")
            with pytest.raises(NotImplementedError):
                p.generate_vision(req)


# ===========================================================================
# 6. Router behavior
# ===========================================================================


class TestRouterBehavior:
    """Test router resolve logic for different purposes."""

    def test_general_uses_config_default(self):
        config = _make_config()
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": FAKE_OPENAI_KEY}):
            router = ModelRouter(config=config)
            provider, model = router.resolve("general")
        assert provider.provider_name == "openai"
        assert model == "gpt-4o-mini"

    def test_general_routes_to_moonshot_when_enabled(self):
        config = LLMConfig.from_dict({
            "enabledProviders": ["openai", "moonshot"],
            "defaults": {
                "general": {"provider": "moonshot", "model": "moonshot-v1-8k"},
                "review": {"provider": "openai", "model": "gpt-4o-mini"},
            },
            "providers": {
                "openai": {"apiBase": "https://api.openai.com/v1"},
                "moonshot": {"apiBase": "https://api.moonshot.cn/v1"},
            },
        })
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": FAKE_OPENAI_KEY}):
            router = ModelRouter(config=config)
            provider, model = router.resolve("general")
        assert provider.provider_name == "moonshot"
        assert model == "moonshot-v1-8k"

    def test_disabled_provider_falls_back_to_openai(self):
        """When moonshot is in defaults but not enabled, falls back to openai."""
        config = LLMConfig.from_dict({
            "enabledProviders": ["openai"],
            "defaults": {
                "general": {"provider": "moonshot", "model": "moonshot-v1-8k"},
                "review": {"provider": "openai", "model": "gpt-4o-mini"},
            },
            "providers": {
                "openai": {"apiBase": "https://api.openai.com/v1"},
            },
        })
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": FAKE_OPENAI_KEY}):
            router = ModelRouter(config=config)
            provider, model = router.resolve("general")
        assert provider.provider_name == "openai"

    def test_unknown_purpose_falls_back_to_openai(self):
        config = _make_config()
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": FAKE_OPENAI_KEY}):
            router = ModelRouter(config=config)
            provider, model = router.resolve("unknown_purpose")
        assert provider.provider_name == "openai"

    def test_get_all_status_returns_all_providers(self):
        config = _make_config()
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": FAKE_OPENAI_KEY}):
            router = ModelRouter(config=config)
            statuses = router.get_all_status()
        names = [s["name"] for s in statuses]
        assert "OpenAI" in names
        assert "Mistral (Codestral)" in names
        assert "Moonshot (Kimi)" in names
        assert "Ollama (Local)" in names

    def test_review_resolve_is_idempotent(self):
        """Multiple calls to resolve(review) return the same provider."""
        config = _make_config()
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": FAKE_OPENAI_KEY}):
            router = ModelRouter(config=config)
            p1, m1 = router.resolve("review")
            p2, m2 = router.resolve("review")
        assert p1 is p2
        assert m1 == m2

    def test_config_init_error_captured(self):
        """Invalid config path results in init_error being set."""
        with mock.patch(
            "src.llm.router.load_llm_config",
            side_effect=LLMConfigError("bad config"),
        ):
            router = ModelRouter(config=None)
            assert router.init_error is not None
            assert "bad config" in router.init_error


# ===========================================================================
# 7. Review fallback — OpenAI quota triggers Codestral fallback
# ===========================================================================


class TestReviewFallback:
    """Review gate fallback: OpenAI transient error -> Mistral Codestral."""

    def test_openai_quota_triggers_codestral_fallback(self):
        """HTTP 429 from OpenAI should trigger Mistral fallback."""
        config = _make_config()
        with mock.patch.dict(os.environ, {
            "OPENAI_API_KEY": FAKE_OPENAI_KEY,
            "MISTRAL_API_KEY": FAKE_MISTRAL_KEY,
        }):
            router = ModelRouter(config=config)

            # Mock OpenAI to raise quota error, Mistral to succeed
            with mock.patch.object(
                router._providers["openai"], "generate_text",
                side_effect=RuntimeError("OpenAI API error: HTTP 429 — rate limited"),
            ):
                with mock.patch.object(
                    router._providers["mistral"], "generate_text",
                    return_value=_fake_mistral_response(),
                ):
                    response = router.generate(LLMRequest(
                        model="", messages=[{"role": "user", "content": "test"}],
                        purpose="review", trace_id="test",
                    ))

            assert response.provider == "mistral"
            assert response.model == "codestral-2501"

    def test_openai_5xx_triggers_fallback(self):
        """HTTP 500/502/503 from OpenAI should trigger fallback."""
        config = _make_config()
        with mock.patch.dict(os.environ, {
            "OPENAI_API_KEY": FAKE_OPENAI_KEY,
            "MISTRAL_API_KEY": FAKE_MISTRAL_KEY,
        }):
            router = ModelRouter(config=config)

            for code in [500, 502, 503, 504]:
                with mock.patch.object(
                    router._providers["openai"], "generate_text",
                    side_effect=RuntimeError(f"OpenAI API error: HTTP {code} — server error"),
                ):
                    with mock.patch.object(
                        router._providers["mistral"], "generate_text",
                        return_value=_fake_mistral_response(),
                    ):
                        response = router.generate(LLMRequest(
                            model="", messages=[{"role": "user", "content": "test"}],
                            purpose="review", trace_id="test",
                        ))
                        assert response.provider == "mistral"

    def test_openai_timeout_triggers_fallback(self):
        """Timeout from OpenAI should trigger fallback."""
        config = _make_config()
        with mock.patch.dict(os.environ, {
            "OPENAI_API_KEY": FAKE_OPENAI_KEY,
            "MISTRAL_API_KEY": FAKE_MISTRAL_KEY,
        }):
            router = ModelRouter(config=config)

            with mock.patch.object(
                router._providers["openai"], "generate_text",
                side_effect=RuntimeError("OpenAI API unreachable: timed out"),
            ):
                with mock.patch.object(
                    router._providers["mistral"], "generate_text",
                    return_value=_fake_mistral_response(),
                ):
                    response = router.generate(LLMRequest(
                        model="", messages=[{"role": "user", "content": "test"}],
                        purpose="review", trace_id="test",
                    ))
                    assert response.provider == "mistral"

    def test_openai_auth_error_does_not_trigger_fallback(self):
        """Non-transient errors (auth, 401) should NOT trigger fallback — fail-closed."""
        config = _make_config()
        with mock.patch.dict(os.environ, {
            "OPENAI_API_KEY": FAKE_OPENAI_KEY,
            "MISTRAL_API_KEY": FAKE_MISTRAL_KEY,
        }):
            router = ModelRouter(config=config)

            with mock.patch.object(
                router._providers["openai"], "generate_text",
                side_effect=RuntimeError("OpenAI API error: HTTP 401 — unauthorized"),
            ):
                with pytest.raises(RuntimeError, match="HTTP 401"):
                    router.generate(LLMRequest(
                        model="", messages=[{"role": "user", "content": "test"}],
                        purpose="review", trace_id="test",
                    ))

    def test_both_reviewers_fail_is_fail_closed(self):
        """If both OpenAI and Mistral fail, review must fail-closed."""
        config = _make_config()
        with mock.patch.dict(os.environ, {
            "OPENAI_API_KEY": FAKE_OPENAI_KEY,
            "MISTRAL_API_KEY": FAKE_MISTRAL_KEY,
        }):
            router = ModelRouter(config=config)

            with mock.patch.object(
                router._providers["openai"], "generate_text",
                side_effect=RuntimeError("OpenAI API error: HTTP 429 — rate limited"),
            ):
                with mock.patch.object(
                    router._providers["mistral"], "generate_text",
                    side_effect=RuntimeError("Mistral API error: HTTP 500 — server error"),
                ):
                    with pytest.raises(RuntimeError, match="FAILED.*fail-closed"):
                        router.generate(LLMRequest(
                            model="", messages=[{"role": "user", "content": "test"}],
                            purpose="review", trace_id="test",
                        ))

    def test_no_fallback_configured_fails_closed(self):
        """If no fallback configured, transient error fails closed with clear message."""
        data = _good_config()
        del data["reviewFallback"]
        config = LLMConfig.from_dict(data)
        with mock.patch.dict(os.environ, {
            "OPENAI_API_KEY": FAKE_OPENAI_KEY,
        }):
            router = ModelRouter(config=config)

            with mock.patch.object(
                router._providers["openai"], "generate_text",
                side_effect=RuntimeError("OpenAI API error: HTTP 429 — rate limited"),
            ):
                with pytest.raises(RuntimeError, match="No fallback reviewer"):
                    router.generate(LLMRequest(
                        model="", messages=[{"role": "user", "content": "test"}],
                        purpose="review", trace_id="test",
                    ))

    def test_fallback_provenance_recorded(self):
        """Response from fallback must have correct provider metadata."""
        config = _make_config()
        with mock.patch.dict(os.environ, {
            "OPENAI_API_KEY": FAKE_OPENAI_KEY,
            "MISTRAL_API_KEY": FAKE_MISTRAL_KEY,
        }):
            router = ModelRouter(config=config)

            with mock.patch.object(
                router._providers["openai"], "generate_text",
                side_effect=RuntimeError("OpenAI API error: HTTP 429 — rate limited"),
            ):
                with mock.patch.object(
                    router._providers["mistral"], "generate_text",
                    return_value=_fake_mistral_response(),
                ):
                    response = router.generate(LLMRequest(
                        model="", messages=[{"role": "user", "content": "test"}],
                        purpose="review", trace_id="test",
                    ))

            # Verify provenance
            assert response.provider == "mistral"
            assert response.model == "codestral-2501"


# ===========================================================================
# 8. Review caps enforcement
# ===========================================================================


class TestReviewCaps:
    """max_output_tokens and temperature are enforced on review calls."""

    def test_max_output_tokens_enforced(self):
        """Review calls must use max_output_tokens from reviewCaps config."""
        config = _make_config()
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": FAKE_OPENAI_KEY}):
            router = ModelRouter(config=config)

            captured_request = {}

            def capture_request(req: LLMRequest) -> LLMResponse:
                captured_request["max_tokens"] = req.max_tokens
                captured_request["temperature"] = req.temperature
                return _fake_openai_response()

            with mock.patch.object(
                router._providers["openai"], "generate_text",
                side_effect=capture_request,
            ):
                router.generate(LLMRequest(
                    model="", messages=[{"role": "user", "content": "test"}],
                    purpose="review", trace_id="test",
                    max_tokens=4096,  # Caller requests more
                    temperature=0.5,  # Caller requests higher
                ))

            # Router should enforce caps, not caller values
            assert captured_request["max_tokens"] == 600
            assert captured_request["temperature"] == 0

    def test_custom_caps_from_config(self):
        """Custom reviewCaps values should be used."""
        data = _good_config()
        data["reviewCaps"] = {"maxOutputTokens": 400, "temperature": 0}
        config = LLMConfig.from_dict(data)
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": FAKE_OPENAI_KEY}):
            router = ModelRouter(config=config)

            captured = {}

            def capture(req):
                captured["max_tokens"] = req.max_tokens
                return _fake_openai_response()

            with mock.patch.object(
                router._providers["openai"], "generate_text",
                side_effect=capture,
            ):
                router.generate(LLMRequest(
                    model="", messages=[{"role": "user", "content": "test"}],
                    purpose="review", trace_id="test",
                ))

            assert captured["max_tokens"] == 400


# ===========================================================================
# 9. Budget cap enforcement
# ===========================================================================


class TestBudgetCap:
    """Budget cap blocks oversized review calls (fail-closed)."""

    def test_budget_blocks_oversized_review(self):
        """If estimated cost exceeds maxUsdPerReview, review is refused."""
        data = _good_config()
        data["budget"]["maxUsdPerReview"] = 0.001  # Very low cap
        config = LLMConfig.from_dict(data)
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": FAKE_OPENAI_KEY}):
            router = ModelRouter(config=config)

            # Large bundle that would blow the budget
            big_content = "x" * 500_000  # ~125K tokens estimated
            with pytest.raises(RuntimeError, match="BUDGET EXCEEDED"):
                router.generate(LLMRequest(
                    model="",
                    messages=[{"role": "user", "content": big_content}],
                    purpose="review", trace_id="test",
                ))

    def test_budget_allows_normal_review(self):
        """Normal-sized review should pass budget check."""
        config = _make_config()
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": FAKE_OPENAI_KEY}):
            router = ModelRouter(config=config)

            with mock.patch.object(
                router._providers["openai"], "generate_text",
                return_value=_fake_openai_response(),
            ):
                # Small bundle — well within budget
                response = router.generate(LLMRequest(
                    model="",
                    messages=[{"role": "user", "content": "small diff"}],
                    purpose="review", trace_id="test",
                ))
                assert response.content

    def test_budget_estimate_cost_function(self):
        """estimate_cost should calculate reasonable estimates."""
        est = estimate_cost(
            model="gpt-4o-mini",
            prompt_text="a" * 4000,  # ~1000 tokens
            max_output_tokens=600,
        )
        assert est.pricing_found
        assert est.estimated_input_tokens == 1000
        assert est.input_cost_usd > 0
        assert est.output_cost_usd > 0
        assert est.estimated_cost_usd == est.input_cost_usd + est.output_cost_usd

    def test_budget_actual_cost_function(self):
        """actual_cost should calculate from real usage."""
        cost = actual_cost(
            model="gpt-4o-mini",
            usage={"prompt_tokens": 1000, "completion_tokens": 200},
        )
        assert cost > 0

    def test_budget_unknown_model_allowed(self):
        """Unknown models should be allowed (no pricing data = no cap enforcement)."""
        est = estimate_cost(
            model="some-unknown-model",
            prompt_text="test",
            max_output_tokens=600,
        )
        assert not est.pricing_found
        allowed, _ = check_budget(est, 0.50, "review")
        assert allowed  # Unknown model = allowed by default


# ===========================================================================
# 10. Transient error detection
# ===========================================================================


class TestTransientErrorDetection:
    """Test _is_transient_error correctly classifies errors."""

    def test_429_is_transient(self):
        assert _is_transient_error(RuntimeError("OpenAI API error: HTTP 429 — rate limited"))

    def test_500_is_transient(self):
        assert _is_transient_error(RuntimeError("OpenAI API error: HTTP 500 — server error"))

    def test_502_is_transient(self):
        assert _is_transient_error(RuntimeError("OpenAI API error: HTTP 502 — bad gateway"))

    def test_503_is_transient(self):
        assert _is_transient_error(RuntimeError("OpenAI API error: HTTP 503 — service unavailable"))

    def test_504_is_transient(self):
        assert _is_transient_error(RuntimeError("OpenAI API error: HTTP 504 — gateway timeout"))

    def test_timeout_is_transient(self):
        assert _is_transient_error(RuntimeError("OpenAI API unreachable: timed out"))

    def test_unreachable_is_transient(self):
        assert _is_transient_error(RuntimeError("OpenAI API unreachable: connection refused"))

    def test_401_is_not_transient(self):
        assert not _is_transient_error(RuntimeError("OpenAI API error: HTTP 401 — unauthorized"))

    def test_400_is_not_transient(self):
        assert not _is_transient_error(RuntimeError("OpenAI API error: HTTP 400 — bad request"))

    def test_parse_error_is_not_transient(self):
        assert not _is_transient_error(RuntimeError("Response parsing failed: invalid JSON"))


# ===========================================================================
# 11. Artifact/log secret scan (post-run simulation)
# ===========================================================================


class TestArtifactSecretScan:
    """Scan simulated outputs for secret patterns."""

    SECRET_PATTERNS = [
        re.compile(r"sk-[A-Za-z0-9_-]{20,}"),          # OpenAI keys
        re.compile(r"Bearer\s+sk-[A-Za-z0-9_-]{10,}"), # Bearer + OpenAI
        re.compile(r"MOONSHOT_API_KEY=[^[\s]{8,}"),     # Moonshot real values
    ]

    def _scan_for_secrets(self, text: str) -> list[str]:
        findings = []
        for pattern in self.SECRET_PATTERNS:
            matches = pattern.findall(text)
            if matches:
                findings.extend(matches)
        return findings

    def test_provider_status_no_secrets(self):
        with mock.patch.dict(os.environ, {
            "OPENAI_API_KEY": FAKE_OPENAI_KEY,
            "MOONSHOT_API_KEY": FAKE_MOONSHOT_KEY,
            "MISTRAL_API_KEY": FAKE_MISTRAL_KEY,
        }):
            config = _make_config({"enabledProviders": ["openai", "moonshot"]})
            router = ModelRouter(config=config)
            statuses = router.get_all_status()
            output = json.dumps(statuses, indent=2)
            findings = self._scan_for_secrets(output)
            assert findings == [], f"Secrets found in status output: {findings}"

    def test_error_output_no_secrets(self):
        error_msgs = [
            redact_for_log(f"OpenAI API error: Bearer {FAKE_OPENAI_KEY}"),
            redact_for_log(f"Failed with key {FAKE_OPENAI_KEY}"),
            redact_for_log(f"MOONSHOT_API_KEY={FAKE_MOONSHOT_KEY}"),
        ]
        for msg in error_msgs:
            findings = self._scan_for_secrets(msg)
            assert findings == [], f"Secret found in error output: {findings}"

    def test_stderr_capture_no_secrets(self):
        config = _make_config()
        captured = io.StringIO()
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": FAKE_OPENAI_KEY}):
            with mock.patch("sys.stderr", captured):
                router = ModelRouter(config=config)
                _ = router.resolve("review")
                _ = router.get_all_status()

        stderr_output = captured.getvalue()
        findings = self._scan_for_secrets(stderr_output)
        assert findings == [], f"Secrets found in stderr: {findings}"


# ===========================================================================
# 12. Types
# ===========================================================================


class TestTypes:
    """Test type construction and serialization."""

    def test_llm_config_from_dict(self):
        config = LLMConfig.from_dict(_good_config())
        assert "openai" in config.enabled_providers
        assert "review" in config.defaults
        assert config.defaults["review"].provider == "openai"

    def test_provider_config_from_dict(self):
        pc = ProviderConfig.from_dict("openai", {
            "apiBase": "https://api.openai.com/v1",
            "keySource": "existing",
        })
        assert pc.name == "openai"
        assert pc.api_base == "https://api.openai.com/v1"

    def test_purpose_route_from_dict(self):
        pr = PurposeRoute.from_dict({"provider": "openai", "model": "gpt-4o"})
        assert pr.provider == "openai"
        assert pr.model == "gpt-4o"

    def test_review_fallback_config_from_dict(self):
        rfc = ReviewFallbackConfig.from_dict({"provider": "mistral", "model": "codestral-2501"})
        assert rfc.provider == "mistral"
        assert rfc.model == "codestral-2501"

    def test_review_caps_config_from_dict(self):
        rcc = ReviewCapsConfig.from_dict({"maxOutputTokens": 400, "temperature": 0})
        assert rcc.max_output_tokens == 400
        assert rcc.temperature == 0

    def test_review_caps_defaults(self):
        rcc = ReviewCapsConfig()
        assert rcc.max_output_tokens == 600
        assert rcc.temperature == 0.0
