"""Shared HQ exec trigger client — single source of truth.

Every Python CLI / ops script that triggers an HQ exec action MUST use
``trigger_exec()`` instead of inline HTTP POST calls.  This ensures
consistent timeout, status-code handling, and structured logging
across all project lanes (Soma, pred_markets, system utilities, …).
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("openclaw.exec_trigger")

# Host executor uses 10/20/40s backoff (total ~70s) before returning 502.
# 90s ensures we don't mark TRIGGER_FAILED while hostd is still probing.
DEFAULT_TRIGGER_TIMEOUT: int = 90

_HQ_BASE_DEFAULT = "http://127.0.0.1:8787"


def _get_hq_base() -> str:
    return os.environ.get("OPENCLAW_HQ_BASE", _HQ_BASE_DEFAULT)


def _resolve_admin_token() -> str:
    token = os.environ.get("OPENCLAW_ADMIN_TOKEN", "")
    if token:
        return token
    for p in (
        "/etc/ai-ops-runner/secrets/openclaw_admin_token",
        "/etc/ai-ops-runner/secrets/openclaw_console_token",
        "/etc/ai-ops-runner/secrets/openclaw_api_token",
        "/etc/ai-ops-runner/secrets/openclaw_token",
    ):
        if Path(p).exists():
            try:
                return Path(p).read_text().strip()
            except OSError:
                continue
    return ""


# ---------------------------------------------------------------------------
# Low-level HTTP helper (replaces per-script ``_curl`` functions)
# ---------------------------------------------------------------------------

def hq_request(
    method: str,
    path: str,
    data: dict | None = None,
    timeout: int = 30,
    base_url: str | None = None,
) -> tuple[int, str]:
    """Issue an HTTP request to HQ.

    Returns ``(status_code, response_body_str)``.
    On network / timeout errors returns ``(-1, error_message)``.
    """
    base = (base_url or _get_hq_base()).rstrip("/")
    url = f"{base}{path}"
    headers: dict[str, str] = {"Content-Type": "application/json"}
    token = _resolve_admin_token()
    if token:
        headers["X-OpenClaw-Token"] = token
    req = urllib.request.Request(url, method=method, headers=headers)
    if data is not None:
        req.data = json.dumps(data).encode("utf-8")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8") if e.fp else ""
    except Exception as e:
        return -1, str(e)


# ---------------------------------------------------------------------------
# Structured trigger result
# ---------------------------------------------------------------------------

@dataclass
class TriggerResult:
    """Outcome of a ``trigger_exec`` call."""

    status_code: int
    state: str  # ACCEPTED | ALREADY_RUNNING | FAILED
    message: str
    run_id: str | None = None
    body: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Primary API — trigger an HQ exec action
# ---------------------------------------------------------------------------

def trigger_exec(
    project: str,
    action: str,
    payload: dict | None = None,
    timeout: int | None = None,
) -> TriggerResult:
    """Trigger an HQ exec action and return a structured result.

    Parameters
    ----------
    project:
        Logical project name for logging (e.g. ``"soma_kajabi"``).
    action:
        The ``action`` value sent to ``POST /api/exec``.
    payload:
        Extra fields merged into the POST body alongside ``action``.
    timeout:
        HTTP timeout in seconds.  Defaults to ``DEFAULT_TRIGGER_TIMEOUT``
        (90 s).  Host executor uses 10 / 20 / 40 s backoff (total ~70 s)
        before returning 502; 90 s ensures we never mark TRIGGER_FAILED
        while hostd is still probing.

    Returns
    -------
    TriggerResult
        ``state`` is one of:

        * ``ACCEPTED`` — 200 or 202; action dispatched.
        * ``ALREADY_RUNNING`` — 409; a run is already in progress.
        * ``FAILED`` — any other status or network error.
    """
    effective_timeout = timeout if timeout is not None else DEFAULT_TRIGGER_TIMEOUT

    body_data: dict[str, Any] = {"action": action}
    if payload:
        body_data.update(payload)

    status_code, raw_body = hq_request(
        "POST", "/api/exec", data=body_data, timeout=effective_timeout,
    )

    parsed: dict[str, Any] = {}
    try:
        parsed = json.loads(raw_body)
    except (json.JSONDecodeError, TypeError):
        pass

    if status_code in (200, 202):
        result = TriggerResult(
            status_code=status_code,
            state="ACCEPTED",
            message=f"Action {action} accepted (HTTP {status_code})",
            run_id=parsed.get("run_id"),
            body=parsed,
        )
    elif status_code == 409:
        result = TriggerResult(
            status_code=409,
            state="ALREADY_RUNNING",
            message=f"Action {action} already running for project={project}",
            run_id=parsed.get("active_run_id"),
            body=parsed,
        )
    else:
        error_detail = (
            parsed.get("error_class")
            or parsed.get("error")
            or raw_body[:200]
        )
        if status_code == -1:
            msg = (
                f"Network/timeout error triggering {action} "
                f"for project={project}: {error_detail}. "
                f"Check hostd/HQ logs."
            )
        else:
            msg = (
                f"Trigger failed for {action} "
                f"(project={project}, HTTP {status_code}): "
                f"{error_detail}. Check hostd/HQ logs."
            )
        result = TriggerResult(
            status_code=status_code,
            state="FAILED",
            message=msg,
            body=parsed if parsed else {},
        )

    logger.info(
        "exec_trigger project=%s action=%s http_status=%d state=%s run_id=%s",
        project,
        action,
        status_code,
        result.state,
        result.run_id or "-",
    )

    return result
