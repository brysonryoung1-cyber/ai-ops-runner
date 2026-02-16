"""Moonshot/Kimi provider — optional, disabled by default.

Uses the Moonshot (Kimi) hosted API at api.moonshot.cn/v1.
Compatible with OpenAI-style chat completions API.
NEVER used for review purpose (hard-pinned to OpenAI).

Key loading: MOONSHOT_API_KEY env var only (no keychain integration yet).
To enable: set "moonshot" in config/llm.json enabledProviders + set MOONSHOT_API_KEY.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from src.llm.provider import BaseProvider, _log, redact_for_log
from src.llm.types import LLMRequest, LLMResponse

DEFAULT_API_BASE = "https://api.moonshot.cn/v1"


def _load_moonshot_key() -> str | None:
    """Load Moonshot API key from environment. NEVER logs the key."""
    return os.environ.get("MOONSHOT_API_KEY", "").strip() or None


def _mask_key(key: str) -> str:
    """Mask key for safe display."""
    if len(key) > 8:
        return key[:4] + "…" + key[-4:]
    return "***"


class MoonshotProvider(BaseProvider):
    """Moonshot/Kimi chat completions provider (OpenAI-compatible API).

    Disabled by default. Must be explicitly enabled in config/llm.json.
    NEVER used for purpose=review (router enforces this).
    """

    def __init__(self, api_base: str = DEFAULT_API_BASE):
        self._api_base = api_base.rstrip("/")

    @property
    def provider_name(self) -> str:
        return "moonshot"

    def is_configured(self) -> bool:
        """Check if Moonshot API key is available."""
        key = _load_moonshot_key()
        return key is not None and len(key) > 0

    def generate_text(self, request: LLMRequest) -> LLMResponse:
        """Call Moonshot chat completions API (OpenAI-compatible)."""
        api_key = _load_moonshot_key()
        if not api_key:
            raise RuntimeError(
                "Moonshot API key not found. Set MOONSHOT_API_KEY env var."
            )

        _log(
            f"Moonshot request: model={request.model} purpose={request.purpose}",
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
                f"Moonshot API error: HTTP {exc.code} — {body}"
            ) from None
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"Moonshot API unreachable: {exc.reason}"
            ) from None
        except Exception as exc:
            raise RuntimeError(
                f"Moonshot API call failed: {redact_for_log(str(exc))}"
            ) from None

        try:
            content = result["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise RuntimeError(
                f"Moonshot response parsing failed: {exc}"
            ) from None

        usage = result.get("usage", {})

        _log(
            f"Moonshot response: model={result.get('model', request.model)} "
            f"tokens={usage.get('total_tokens', '?')}",
            trace_id=request.trace_id,
        )

        return LLMResponse(
            content=content,
            model=result.get("model", request.model),
            provider="moonshot",
            usage={
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            },
            trace_id=request.trace_id,
            raw=result,
        )

    def get_status(self) -> dict:
        """Return status dict with masked fingerprint."""
        key = _load_moonshot_key()
        configured = key is not None and len(key) > 0

        return {
            "name": "Moonshot (Kimi)",
            "enabled": False,  # Will be set by router based on config
            "configured": configured,
            "status": "active" if configured else "disabled",
            "fingerprint": _mask_key(key) if configured and key else None,
            "api_base": self._api_base,
        }
