"""State machine for Soma Auto-Finish. Persists stage.json and SUMMARY.md per stage."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


STAGES = [
    "connectors_status",
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
    "SESSION_CHECK_TIMEOUT",  # login required
    "SESSION_CHECK_BROWSER_CLOSED",
})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    """Write stage.json for current stage."""
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
