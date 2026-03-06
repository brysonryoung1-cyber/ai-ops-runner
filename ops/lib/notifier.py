"""Outbound notifier helpers (Discord webhook only)."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any
from urllib import error, request
from urllib.parse import urlparse

DISCORD_WEBHOOK_ENV_VARS = (
    "OPENCLAW_DISCORD_WEBHOOK_URL",
    "DISCORD_WEBHOOK_URL",
    "DISCORD_WEBHOOK",
)
DISCORD_WEBHOOK_SECRET_FILE = Path("/etc/ai-ops-runner/secrets/discord_webhook_url")
DISCORD_WEBHOOK_CONFIG_FILE = Path("/etc/ai-ops-runner/config/discord_webhook_url")


def resolve_discord_webhook_url() -> tuple[str | None, str]:
    """Resolve Discord webhook URL from env or secret file."""

    for env_name in DISCORD_WEBHOOK_ENV_VARS:
        env_url = os.environ.get(env_name, "").strip()
        if env_url:
            return env_url, "env"

    saw_file_error = False
    for candidate in (DISCORD_WEBHOOK_SECRET_FILE, DISCORD_WEBHOOK_CONFIG_FILE):
        try:
            if not candidate.exists():
                continue
            file_url = candidate.read_text(encoding="utf-8").strip()
            if file_url:
                return file_url, "file"
        except OSError:
            saw_file_error = True

    if saw_file_error:
        return None, "file_error"

    return None, "missing"


def build_alert_hash(*, event_type: str, matrix_status: str, failed_checks: list[str]) -> str:
    """Compute stable alert dedupe hash from state-change payload."""

    canonical = {
        "event_type": str(event_type),
        "matrix_status": str(matrix_status),
        "failed_checks": sorted({str(item) for item in failed_checks if str(item).strip()}),
    }
    raw = json.dumps(canonical, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _is_valid_webhook_url(raw_url: str | None) -> bool:
    if not isinstance(raw_url, str):
        return False
    text = raw_url.strip()
    if not text:
        return False
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"}:
        return False
    if not parsed.netloc:
        return False
    return bool(parsed.path.strip("/"))


def send_discord_webhook_alert(
    *,
    content: str,
    timeout_sec: int = 10,
) -> dict[str, Any]:
    """Send Discord webhook alert without ever logging/storing secrets."""

    webhook_url, source = resolve_discord_webhook_url()
    if not _is_valid_webhook_url(webhook_url):
        return {
            "ok": False,
            "error_class": "DISCORD_WEBHOOK_INVALID",
            "source": source,
            "status_code": None,
            "message": "Discord webhook URL is missing or invalid.",
        }

    body = json.dumps({"content": content}).encode("utf-8")
    try:
        req = request.Request(
            webhook_url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with request.urlopen(req, timeout=timeout_sec) as resp:
            status = int(resp.getcode() or 0)
            if 200 <= status < 300:
                return {
                    "ok": True,
                    "source": source,
                    "status_code": status,
                    "message": "",
                }
            return {
                "ok": False,
                "error_class": "DISCORD_HTTP_ERROR",
                "status_code": status,
                "source": source,
                "message": f"Discord webhook returned HTTP {status}.",
            }
    except ValueError as exc:
        return {
            "ok": False,
            "error_class": "DISCORD_WEBHOOK_INVALID",
            "source": source,
            "status_code": None,
            "message": str(exc) or "Discord webhook URL is invalid.",
        }
    except error.HTTPError as exc:
        return {
            "ok": False,
            "error_class": "DISCORD_HTTP_ERROR",
            "status_code": int(exc.code),
            "source": source,
            "message": str(exc) or f"Discord webhook returned HTTP {int(exc.code)}.",
        }
    except error.URLError as exc:
        reason = getattr(exc, "reason", None)
        return {
            "ok": False,
            "error_class": "DISCORD_URL_ERROR",
            "source": source,
            "status_code": None,
            "message": str(reason or exc) or "Discord webhook URL request failed.",
        }
    except TimeoutError as exc:
        return {
            "ok": False,
            "error_class": "DISCORD_TIMEOUT",
            "source": source,
            "status_code": None,
            "message": str(exc) or "Discord webhook request timed out.",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error_class": "DISCORD_UNKNOWN",
            "source": source,
            "status_code": None,
            "message": str(exc) or type(exc).__name__,
        }
