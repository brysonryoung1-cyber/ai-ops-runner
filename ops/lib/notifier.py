"""Outbound notifier helpers (Discord webhook only)."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any
from urllib import error, request

DISCORD_WEBHOOK_SECRET_FILE = Path("/etc/ai-ops-runner/secrets/discord_webhook_url")


def resolve_discord_webhook_url() -> tuple[str | None, str]:
    """Resolve Discord webhook URL from env or secret file."""

    env_url = os.environ.get("OPENCLAW_DISCORD_WEBHOOK_URL", "").strip()
    if env_url:
        return env_url, "env"

    try:
        if DISCORD_WEBHOOK_SECRET_FILE.exists():
            file_url = DISCORD_WEBHOOK_SECRET_FILE.read_text(encoding="utf-8").strip()
            if file_url:
                return file_url, "file"
    except OSError:
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


def send_discord_webhook_alert(
    *,
    content: str,
    timeout_sec: int = 10,
) -> dict[str, Any]:
    """Send Discord webhook alert without ever logging/storing secrets."""

    webhook_url, source = resolve_discord_webhook_url()
    if not webhook_url:
        return {
            "ok": False,
            "error_class": "DISCORD_WEBHOOK_MISSING",
            "source": source,
        }

    body = json.dumps({"content": content}).encode("utf-8")
    req = request.Request(
        webhook_url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with request.urlopen(req, timeout=timeout_sec) as resp:
            status = int(resp.getcode() or 0)
            if 200 <= status < 300:
                return {"ok": True, "http_code": status, "source": source}
            return {
                "ok": False,
                "error_class": "DISCORD_HTTP_STATUS",
                "http_code": status,
                "source": source,
            }
    except error.HTTPError as exc:
        return {
            "ok": False,
            "error_class": "DISCORD_HTTP_ERROR",
            "http_code": int(exc.code),
            "source": source,
        }
    except error.URLError as exc:
        reason = getattr(exc, "reason", None)
        return {
            "ok": False,
            "error_class": "DISCORD_URL_ERROR",
            "reason_type": type(reason).__name__ if reason is not None else "unknown",
            "source": source,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error_class": "DISCORD_POST_EXCEPTION",
            "exception_type": type(exc).__name__,
            "source": source,
        }
