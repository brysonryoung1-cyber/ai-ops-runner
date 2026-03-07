"""Transition-based Discord notification router with dedupe."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from ops.lib.notifier import send_discord_webhook_alert


def _repo_root() -> Path:
    env_root = os.environ.get("OPENCLAW_REPO_ROOT", "").strip()
    if env_root:
        return Path(env_root).expanduser()
    return Path(__file__).resolve().parents[2]


def _artifacts_root() -> Path:
    env_root = os.environ.get("OPENCLAW_ARTIFACTS_ROOT", "").strip()
    if env_root:
        return Path(env_root).expanduser()
    repo_root = _repo_root().resolve()
    if str(repo_root) == "/opt/ai-ops-runner" or str(repo_root).startswith("/opt/ai-ops-runner/"):
        return Path("/opt/ai-ops-runner/artifacts")
    return repo_root / "artifacts"


def _state_path() -> Path:
    return _artifacts_root() / "system" / "notification_router_state.json"


def _read_state() -> dict[str, Any]:
    path = _state_path()
    try:
        if not path.exists():
            return {"events": {}}
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            events = data.get("events")
            if isinstance(events, dict):
                return {"events": {str(k): str(v) for k, v in events.items()}}
    except (OSError, json.JSONDecodeError):
        pass
    return {"events": {}}


def _write_state(state: dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=str(path.parent), delete=False) as tmp:
        json.dump(state, tmp, indent=2)
        tmp.write("\n")
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def build_state_hash(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _hq_base() -> str:
    return (
        os.environ.get("OPENCLAW_CANONICAL_URL", "").strip()
        or os.environ.get("OPENCLAW_HQ_BASE", "").strip()
        or "http://127.0.0.1:8787"
    ).rstrip("/")


def send_transition_notification(
    *,
    project_id: str,
    event_type: str,
    state_hash: str,
    summary: str,
    proof_path: str | None = None,
    hq_path: str | None = None,
) -> dict[str, Any]:
    state = _read_state()
    dedupe_key = f"{project_id}:{event_type}"
    if str(state.get("events", {}).get(dedupe_key) or "") == state_hash:
        return {
            "ok": False,
            "deduped": True,
            "state_hash": state_hash,
            "message": "Notification already sent for this state.",
        }

    lines = [
        f"OpenClaw {event_type}",
        f"project: {project_id}",
        summary,
    ]
    if proof_path:
        lines.append(f"proof: {proof_path}")
    if hq_path:
        lines.append(f"hq: {_hq_base()}{hq_path}")
    notify = send_discord_webhook_alert(content="\n".join(lines))
    if notify.get("ok"):
        events = state.get("events")
        if not isinstance(events, dict):
            events = {}
        events[dedupe_key] = state_hash
        state["events"] = events
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        _write_state(state)
    notify["deduped"] = False
    notify["state_hash"] = state_hash
    return notify
