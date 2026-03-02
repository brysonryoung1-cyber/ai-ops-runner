"""Human gate state management for login windows.

Provides read/write/clear for a per-project gate file that signals
when a human login window is active. Scripts that would disruptively
restart noVNC check this gate and suppress remediation.

State file: <state_dir>/<project_id>.json
Artifacts:  artifacts/<project_id>/human_gate/<run_id>/HUMAN_GATE.json
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

_DEFAULT_STATE_ROOT = "/opt/ai-ops-runner/state"
_DEFAULT_TTL_MINUTES = 15


def _state_dir() -> Path:
    root = os.environ.get("OPENCLAW_STATE_ROOT", _DEFAULT_STATE_ROOT)
    return Path(root) / "human_gate"


def _gate_path(project_id: str) -> Path:
    return _state_dir() / f"{project_id}.json"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _repo_root() -> Path:
    env = os.environ.get("OPENCLAW_REPO_ROOT")
    if env and Path(env).exists():
        return Path(env)
    return Path("/opt/ai-ops-runner")


def read_gate(project_id: str) -> dict[str, Any]:
    """Read gate state. Auto-expires if past expires_at.

    Returns {"active": bool, "gate": dict|None}.
    """
    path = _gate_path(project_id)
    if not path.exists():
        return {"active": False, "gate": None}
    try:
        gate = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {"active": False, "gate": None}

    expires_at_str = gate.get("expires_at")
    if not expires_at_str:
        return {"active": False, "gate": None}

    try:
        expires_at = datetime.fromisoformat(expires_at_str)
    except (ValueError, TypeError):
        return {"active": False, "gate": None}

    if _now_utc() >= expires_at:
        clear_gate(project_id)
        return {"active": False, "gate": None}

    return {"active": True, "gate": gate}


def write_gate(
    project_id: str,
    run_id: str,
    novnc_url: str,
    reason: str,
    ttl_minutes: int = _DEFAULT_TTL_MINUTES,
) -> dict[str, Any]:
    """Write (or overwrite) gate state. Returns the gate dict."""
    now = _now_utc()
    expires_at = now + timedelta(minutes=ttl_minutes)
    gate = {
        "active": True,
        "project_id": project_id,
        "run_id": run_id,
        "novnc_url": novnc_url,
        "reason": reason,
        "created_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
        "ttl_minutes": ttl_minutes,
    }

    path = _gate_path(project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(gate, indent=2))

    return gate


def clear_gate(project_id: str) -> None:
    """Remove gate state file."""
    path = _gate_path(project_id)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def write_gate_artifact(
    project_id: str,
    run_id: str,
    gate: dict[str, Any],
) -> Path:
    """Write HUMAN_GATE.json audit artifact. Returns the artifact path."""
    root = _repo_root()
    artifact_dir = root / "artifacts" / project_id / "human_gate" / run_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = artifact_dir / "HUMAN_GATE.json"
    artifact_path.write_text(json.dumps(gate, indent=2))
    return artifact_path


def is_gate_active(project_id: str) -> bool:
    """Quick check: is the login window currently active?"""
    return read_gate(project_id).get("active", False)
