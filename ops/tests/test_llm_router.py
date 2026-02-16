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

from src.llm.types import LLMConfig, PurposeRoute, ProviderConfig, LLMRequest
from src.llm.config import validate_llm_config, load_llm_config, LLMConfigError
from src.llm.router import ModelRouter, reset_router
from src.llm.provider import BaseProvider, redact_for_log
from src.llm.openai_provider import OpenAIProvider, CODEX_REVIEW_MODEL, _mask_key
from src.llm.moonshot_provider import MoonshotProvider
from src.llm.ollama_provider import OllamaProvider

FAKE_OPENAI_KEY = "sk-test-FAKE-000000000000000000000000000000000000"
FAKE_MOONSHOT_KEY = "msk-test-FAKE-0000000000000000000000000000000000"


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
            "general": {"provider": "openai", "model": "gpt-4o"},
            "review": {"provider": "openai", "model": "gpt-4o"},
        },
        "providers": {
            "openai": {
                "apiBase": "https://api.openai.com/v1",
                "keySource": "existing_secret_store",
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
                "review": {"provider": "openai", "model": "gpt-4o"},
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
                "review": {"provider": "openai", "model": "gpt-4o"},
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
            # Need to reload the module to pick up the env var change
            import src.llm.openai_provider as oai_mod
            importlib.reload(oai_mod)
            assert oai_mod.CODEX_REVIEW_MODEL == "gpt-5.3-codex"
        # Restore default
        with mock.patch.dict(os.environ, {"OPENCLAW_REVIEW_MODEL": "gpt-4o"}):
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
            # Fingerprint should be present but masked
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
        assert model == "gpt-4o"

    def test_general_routes_to_moonshot_when_enabled(self):
        config = LLMConfig.from_dict({
            "enabledProviders": ["openai", "moonshot"],
            "defaults": {
                "general": {"provider": "moonshot", "model": "moonshot-v1-8k"},
                "review": {"provider": "openai", "model": "gpt-4o"},
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
                "review": {"provider": "openai", "model": "gpt-4o"},
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
# 7. Artifact/log secret scan (post-run simulation)
# ===========================================================================


class TestArtifactSecretScan:
    """Scan simulated outputs for secret patterns."""

    SECRET_PATTERNS = [
        re.compile(r"sk-[A-Za-z0-9_-]{20,}"),          # OpenAI keys
        re.compile(r"Bearer\s+sk-[A-Za-z0-9_-]{10,}"), # Bearer + OpenAI
        re.compile(r"MOONSHOT_API_KEY=[^[\s]{8,}"),     # Moonshot real values (not [REDACTED])
    ]

    def _scan_for_secrets(self, text: str) -> list[str]:
        """Return list of secret pattern matches found in text."""
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
        }):
            config = _make_config({"enabledProviders": ["openai", "moonshot"]})
            router = ModelRouter(config=config)
            statuses = router.get_all_status()
            output = json.dumps(statuses, indent=2)
            findings = self._scan_for_secrets(output)
            assert findings == [], f"Secrets found in status output: {findings}"

    def test_error_output_no_secrets(self):
        """Simulated error output must not contain secrets."""
        error_msgs = [
            redact_for_log(f"OpenAI API error: Bearer {FAKE_OPENAI_KEY}"),
            redact_for_log(f"Failed with key {FAKE_OPENAI_KEY}"),
            redact_for_log(f"MOONSHOT_API_KEY={FAKE_MOONSHOT_KEY}"),
        ]
        for msg in error_msgs:
            findings = self._scan_for_secrets(msg)
            assert findings == [], f"Secret found in error output: {findings}"

    def test_stderr_capture_no_secrets(self):
        """Capture stderr during router operations and check for secrets."""
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
# 8. Types
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
