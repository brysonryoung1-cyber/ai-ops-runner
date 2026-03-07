"""Transition-based Discord notification router with per-project dedupe state."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from ops.lib.artifacts_root import get_artifacts_root
from ops.lib.notifier import send_discord_webhook_alert

MAX_NOTIFICATION_TEXT = 1800
MAX_ERROR_MESSAGE = 240


def _repo_root() -> Path:
    env_root = os.environ.get("OPENCLAW_REPO_ROOT", "").strip()
    if env_root:
        return Path(env_root).expanduser()
    return Path(__file__).resolve().parents[2]


def _artifacts_root() -> Path:
    return get_artifacts_root(repo_root=_repo_root().resolve())


def _transitions_dir() -> Path:
    return _artifacts_root() / "system" / "transitions"


def _transition_path(project_id: str) -> Path:
    safe_project_id = str(project_id or "").strip().replace("/", "_")
    return _transitions_dir() / f"{safe_project_id}.json"


def _bounded_text(value: Any, *, max_len: int = MAX_ERROR_MESSAGE) -> str:
    text = str(value or "").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 3].rstrip() + "..."


def read_transition_store(project_id: str) -> dict[str, Any]:
    path = _transition_path(project_id)
    default = {
        "project_id": str(project_id),
        "last_hash": None,
        "last_sent_events": {},
        "updated_at": None,
    }
    try:
        if not path.exists():
            return default
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return default
        sent = data.get("last_sent_events")
        return {
            **data,
            "project_id": str(data.get("project_id") or project_id),
            "last_hash": data.get("last_hash"),
            "last_sent_events": {
                str(key): str(value)
                for key, value in (sent.items() if isinstance(sent, dict) else {})
                if str(key).strip() and str(value).strip()
            },
            "updated_at": data.get("updated_at"),
        }
    except (OSError, json.JSONDecodeError):
        return default


def write_transition_store(project_id: str, store: dict[str, Any]) -> Path:
    path = _transition_path(project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        **store,
        "project_id": str(project_id),
        "last_sent_events": {
            str(key): str(value)
            for key, value in (
                store.get("last_sent_events", {}).items()
                if isinstance(store.get("last_sent_events"), dict)
                else {}
            )
            if str(key).strip() and str(value).strip()
        },
        "updated_at": str(store.get("updated_at") or datetime.now(timezone.utc).isoformat()),
    }
    with NamedTemporaryFile("w", encoding="utf-8", dir=str(path.parent), delete=False) as tmp:
        json.dump(payload, tmp, indent=2, sort_keys=True)
        tmp.write("\n")
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)
    return path


def build_state_hash(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _hq_base() -> str:
    return (
        os.environ.get("OPENCLAW_CANONICAL_URL", "").strip()
        or os.environ.get("OPENCLAW_HQ_BASE", "").strip()
        or "http://127.0.0.1:8787"
    ).rstrip("/")


def _render_hq_link(hq_path: str | None) -> str | None:
    text = str(hq_path or "").strip()
    if not text:
        return None
    if text.startswith("http://") or text.startswith("https://"):
        return text
    return f"{_hq_base()}{text if text.startswith('/') else '/' + text}"


def _render_notification_content(
    *,
    project_id: str,
    event_type: str,
    summary: str,
    proof_path: str | None,
    hq_path: str | None,
) -> str:
    lines = [
        f"OpenClaw {event_type}",
        f"project: {project_id}",
        _bounded_text(summary, max_len=480),
    ]
    if proof_path:
        lines.append(f"proof_path: {_bounded_text(proof_path, max_len=320)}")
    hq_link = _render_hq_link(hq_path)
    if hq_link:
        lines.append(f"hq: {hq_link}")
    content = "\n".join(lines)
    if len(content) <= MAX_NOTIFICATION_TEXT:
        return content
    return content[: MAX_NOTIFICATION_TEXT - 3].rstrip() + "..."


def send_transition_notification(
    *,
    project_id: str,
    event_type: str,
    state_hash: str,
    summary: str,
    proof_path: str | None = None,
    hq_path: str | None = None,
) -> dict[str, Any]:
    try:
        store = read_transition_store(project_id)
        last_sent_events = dict(store.get("last_sent_events") or {})
        if str(last_sent_events.get(event_type) or "") == str(state_hash):
            return {
                "ok": False,
                "deduped": True,
                "status": "DEDUPED",
                "state_hash": state_hash,
                "message": "Notification already sent for this state.",
            }

        notify = send_discord_webhook_alert(
            content=_render_notification_content(
                project_id=project_id,
                event_type=event_type,
                summary=summary,
                proof_path=proof_path,
                hq_path=hq_path,
            )
        )

        store["last_hash"] = state_hash
        store["updated_at"] = datetime.now(timezone.utc).isoformat()
        if notify.get("ok"):
            last_sent_events[event_type] = state_hash
            store["last_sent_events"] = last_sent_events
        write_transition_store(project_id, store)

        notify["deduped"] = False
        notify["state_hash"] = state_hash
        notify["status"] = "SENT" if notify.get("ok") else "ERROR"
        notify["message"] = _bounded_text(notify.get("message"), max_len=MAX_ERROR_MESSAGE)
        return notify
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "deduped": False,
            "status": "ERROR",
            "state_hash": state_hash,
            "error_class": "NOTIFICATION_ROUTER_ERROR",
            "message": _bounded_text(str(exc) or type(exc).__name__, max_len=MAX_ERROR_MESSAGE),
        }
