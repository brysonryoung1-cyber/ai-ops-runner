"""Deterministic state machine for Soma Auto-Finish. Persisted to artifacts for resumability.

Stages: PRECHECK → CONNECTORS_STATUS → SESSION_CHECK → CAPTURE_INTERACTIVE (if auth needed)
       → PHASE0 → FINISH_PLAN → ACCEPTANCE_GATE → DONE

HARD RULE: AUTH_NEEDED_ERROR_CLASSES must NEVER hard-fail; they trigger WAITING_FOR_HUMAN.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STAGES = [
    "precheck",
    "connectors_status",
    "session_warm_status",
    "session_check",
    "capture_interactive",
    "phase0",
    "finish_plan",
    "acceptance_gate",
    "done",
]

AUTH_NEEDED_ERROR_CLASSES = frozenset({
    "KAJABI_CAPTURE_INTERACTIVE_FAILED",
    "KAJABI_CLOUDFLARE_BLOCKED",
    "KAJABI_NOT_LOGGED_IN",
    "KAJABI_SESSION_EXPIRED",
    "SESSION_CHECK_TIMEOUT",
    "SESSION_CHECK_BROWSER_CLOSED",
    "KAJABI_INTERACTIVE_CAPTURE_ERROR",
    "KAJABI_INTERACTIVE_CAPTURE_TIMEOUT",
})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def state_path(out_dir: Path) -> Path:
    return out_dir / "state.json"


def write_state(
    out_dir: Path,
    stage: str,
    status: str,
    *,
    started_at: str | None = None,
    finished_at: str | None = None,
    retries: int = 0,
    last_error_class: str | None = None,
    last_error_summary: str | None = None,
    next_action_hint: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Persist state on every transition. Enables resumability and debugging."""
    data: dict[str, Any] = {
        "stage": stage,
        "status": status,
        "started_at": started_at or _now_iso(),
        "finished_at": finished_at or _now_iso(),
        "retries": retries,
        "last_error_class": last_error_class,
        "last_error_summary": last_error_summary,
        "next_action_hint": next_action_hint,
    }
    if extra:
        data.update(extra)
    state_path(out_dir).write_text(json.dumps(data, indent=2))


def write_stage(
    out_dir: Path,
    stage: str,
    status: str,
    *,
    started_at: str | None = None,
    finished_at: str | None = None,
    retries: int = 0,
    last_error_class: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Write stage.json (legacy) and state.json for current stage."""
    write_state(
        out_dir,
        stage,
        status,
        started_at=started_at,
        finished_at=finished_at,
        retries=retries,
        last_error_class=last_error_class,
        extra=extra,
    )
    stage_data: dict[str, Any] = {
        "stage": stage,
        "status": status,
        "started_at": started_at or _now_iso(),
        "finished_at": finished_at or _now_iso(),
        "retries": retries,
        "last_error_class": last_error_class,
    }
    if extra:
        stage_data.update(extra)
    (out_dir / "stage.json").write_text(json.dumps(stage_data, indent=2))


def append_summary_line(out_dir: Path, line: str) -> None:
    """Append single line to SUMMARY.md (stage log)."""
    summary_path = out_dir / "SUMMARY.md"
    if summary_path.exists():
        content = summary_path.read_text()
    else:
        content = ""
    content = content.rstrip()
    if content:
        content += "\n"
    content += line + "\n"
    summary_path.write_text(content)


def write_result_json(
    out_dir: Path,
    status: str,
    *,
    run_id: str,
    stage: str | None = None,
    error_class: str | None = None,
    message: str | None = None,
    novnc_url: str | None = None,
    instruction_line: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Write RESULT.json with terminal status. Always called in finally block.
    status: SUCCESS | WAITING_FOR_HUMAN | FAILURE | TIMEOUT
    """
    data: dict[str, Any] = {
        "status": status,
        "run_id": run_id,
        "timestamp_utc": _now_iso(),
    }
    if stage:
        data["stage"] = stage
    if error_class:
        data["error_class"] = error_class
    if message:
        data["message"] = message
    if novnc_url:
        data["novnc_url"] = novnc_url
    if instruction_line:
        data["instruction_line"] = instruction_line
    if extra:
        data.update(extra)
    (out_dir / "RESULT.json").write_text(json.dumps(data, indent=2))


def is_auth_needed_error(error_class: str | None) -> bool:
    """True if error indicates login/Cloudflare/challenge — must NOT hard-fail."""
    if not error_class:
        return False
    return error_class in AUTH_NEEDED_ERROR_CLASSES or (
        "login" in error_class.lower()
        or "cloudflare" in error_class.lower()
        or "challenge" in error_class.lower()
        or "forbidden" in error_class.lower()
    )
