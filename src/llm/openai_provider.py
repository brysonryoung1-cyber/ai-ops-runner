"""OpenAI provider — uses existing hardened key-loading from ops/openai_key.py.

This provider is HARD-PINNED for review purpose. No fallback, no override.
Uses urllib (no external HTTP dependencies) consistent with existing repo patterns.
Keys are never logged. All errors are redacted before output.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from src.llm.provider import BaseProvider, _log, redact_for_log
from src.llm.types import LLMRequest, LLMResponse

# Hard-pinned review model constant — env override for testing/migration only
# Uses gpt-4o-mini: code-capable, chat-completions compatible, 16x cheaper than gpt-4o
CODEX_REVIEW_MODEL = os.environ.get("OPENCLAW_REVIEW_MODEL", "gpt-4o-mini")

# Default API base
DEFAULT_API_BASE = "https://api.openai.com/v1"


def _load_openai_key() -> str | None:
    """Load OpenAI API key using the repo's hardened key-loading flow.

    Resolution order (from ops/openai_key.py):
      1. OPENAI_API_KEY env var
      2. Python keyring (macOS Keychain / Linux SecretService)
      3. Linux /etc/ai-ops-runner/secrets/openai_api_key

    Returns None if unavailable. NEVER raises for missing key (caller decides).
    NEVER logs the key.
    """
    # Fast path: env var
    env_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if env_key:
        return env_key

    # Import the hardened key loader (if available)
    try:
        ops_dir = Path(__file__).resolve().parent.parent.parent / "ops"
        if ops_dir.is_dir():
            sys.path.insert(0, str(ops_dir.parent))
            try:
                from ops.openai_key import resolve_key
                key = resolve_key()
                if key:
                    return key
            except ImportError:
                pass
            finally:
                if str(ops_dir.parent) in sys.path:
                    sys.path.remove(str(ops_dir.parent))
    except Exception:
        pass

    return None


def _mask_key(key: str) -> str:
    """Mask a key for safe display: 'sk-…abcd'."""
    if len(key) > 8:
        return key[:3] + "…" + key[-4:]
    return "***"


class OpenAIProvider(BaseProvider):
    """OpenAI chat completions provider.

    Uses the repo's existing key-loading mechanism (env → keychain → linux file).
    Compatible with the existing review pipeline (openclaw_codex_review.sh).
    """

    def __init__(self, api_base: str = DEFAULT_API_BASE):
        self._api_base = api_base.rstrip("/")

    @property
    def provider_name(self) -> str:
        return "openai"

    def is_configured(self) -> bool:
        """Check if OpenAI API key is available. NEVER logs the key."""
        key = _load_openai_key()
        return key is not None and len(key) > 0

    def generate_text(self, request: LLMRequest) -> LLMResponse:
        """Call OpenAI chat completions API.

        Uses urllib (no external HTTP dep) consistent with existing patterns.
        Raises RuntimeError on any failure (fail-closed).
        """
        api_key = _load_openai_key()
        if not api_key:
            raise RuntimeError(
                "OpenAI API key not found. Set OPENAI_API_KEY env var, "
                "or run: python3 ops/openai_key.py set"
            )

        _log(
            f"OpenAI request: model={request.model} purpose={request.purpose}",
            trace_id=request.trace_id,
        )

        payload: dict = {
            "model": request.model,
            "temperature": request.temperature,
            "messages": request.messages,
        }
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        if request.response_format:
            payload["response_format"] = request.response_format

        url = f"{self._api_base}/chat/completions"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                raw = exc.read().decode("utf-8", errors="replace")[:500]
                body = redact_for_log(raw)
            except Exception:
                body = "(unreadable)"
            raise RuntimeError(
                f"OpenAI API error: HTTP {exc.code} — {body}"
            ) from None
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"OpenAI API unreachable: {exc.reason}"
            ) from None
        except Exception as exc:
            raise RuntimeError(
                f"OpenAI API call failed: {redact_for_log(str(exc))}"
            ) from None

        try:
            content = result["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise RuntimeError(
                f"OpenAI response parsing failed: {exc}"
            ) from None

        usage = result.get("usage", {})

        _log(
            f"OpenAI response: model={result.get('model', request.model)} "
            f"tokens={usage.get('total_tokens', '?')}",
            trace_id=request.trace_id,
        )

        return LLMResponse(
            content=content,
            model=result.get("model", request.model),
            provider="openai",
            usage={
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            },
            trace_id=request.trace_id,
            raw=result,
        )

    def get_status(self) -> dict:
        """Return status dict with masked fingerprint. NEVER exposes key."""
        key = _load_openai_key()
        configured = key is not None and len(key) > 0

        return {
            "name": "OpenAI",
            "enabled": True,
            "configured": configured,
            "status": "active" if configured else "inactive",
            "fingerprint": _mask_key(key) if configured and key else None,
            "api_base": self._api_base,
            "review_model": CODEX_REVIEW_MODEL,
        }
