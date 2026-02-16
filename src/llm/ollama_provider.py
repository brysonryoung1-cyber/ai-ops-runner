"""Ollama provider — optional local LLM, disabled by default.

Uses Ollama's OpenAI-compatible API at 127.0.0.1:11434.
Bound to localhost only (private-only — no public port exposure).
NEVER used for review purpose (hard-pinned to OpenAI).

To enable: set "ollama" in config/llm.json enabledProviders + run Ollama locally.
No API key required (local-only).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from src.llm.provider import BaseProvider, _log, redact_for_log
from src.llm.types import LLMRequest, LLMResponse

# Ollama MUST bind to localhost only (private-only invariant)
DEFAULT_API_BASE = "http://127.0.0.1:11434"


class OllamaProvider(BaseProvider):
    """Ollama local LLM provider (OpenAI-compatible API).

    Disabled by default. Must be explicitly enabled in config/llm.json.
    NEVER used for purpose=review (router enforces this).
    Binds to 127.0.0.1 only — no public port exposure.
    """

    def __init__(self, api_base: str = DEFAULT_API_BASE):
        self._api_base = api_base.rstrip("/")
        # Enforce localhost-only binding
        if not self._is_localhost():
            raise ValueError(
                f"Ollama API base must be localhost (127.0.0.1 or ::1), "
                f"got: {self._api_base}. Public Ollama endpoints are not allowed."
            )

    def _is_localhost(self) -> bool:
        """Check that the API base is localhost-bound."""
        base = self._api_base.lower()
        return (
            "127.0.0.1" in base
            or "localhost" in base
            or "[::1]" in base
        )

    @property
    def provider_name(self) -> str:
        return "ollama"

    def is_configured(self) -> bool:
        """Check if Ollama is reachable on localhost. No API key needed."""
        try:
            req = urllib.request.Request(
                f"{self._api_base}/api/tags",
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                resp.read(256)
            return True
        except Exception:
            return False

    def generate_text(self, request: LLMRequest) -> LLMResponse:
        """Call Ollama's OpenAI-compatible chat completions API."""
        _log(
            f"Ollama request: model={request.model} purpose={request.purpose}",
            trace_id=request.trace_id,
        )

        payload: dict = {
            "model": request.model,
            "temperature": request.temperature,
            "messages": request.messages,
            "stream": False,
        }
        if request.max_tokens is not None:
            payload["options"] = {"num_predict": request.max_tokens}

        url = f"{self._api_base}/v1/chat/completions"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                result = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                raw = exc.read().decode("utf-8", errors="replace")[:500]
                body = redact_for_log(raw)
            except Exception:
                body = "(unreadable)"
            raise RuntimeError(
                f"Ollama API error: HTTP {exc.code} — {body}"
            ) from None
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"Ollama not reachable at {self._api_base}: {exc.reason}"
            ) from None
        except Exception as exc:
            raise RuntimeError(
                f"Ollama API call failed: {redact_for_log(str(exc))}"
            ) from None

        try:
            content = result["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise RuntimeError(
                f"Ollama response parsing failed: {exc}"
            ) from None

        usage = result.get("usage", {})

        _log(
            f"Ollama response: model={result.get('model', request.model)} "
            f"tokens={usage.get('total_tokens', '?')}",
            trace_id=request.trace_id,
        )

        return LLMResponse(
            content=content,
            model=result.get("model", request.model),
            provider="ollama",
            usage={
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            },
            trace_id=request.trace_id,
            raw=result,
        )

    def get_status(self) -> dict:
        """Return status dict. No secrets to mask (local-only)."""
        reachable = False
        try:
            reachable = self.is_configured()
        except Exception:
            pass

        return {
            "name": "Ollama (Local)",
            "enabled": False,  # Will be set by router based on config
            "configured": reachable,
            "status": "active" if reachable else "disabled",
            "fingerprint": None,
            "api_base": self._api_base,
        }
