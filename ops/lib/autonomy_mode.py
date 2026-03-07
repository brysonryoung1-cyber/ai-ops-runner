"""Autonomy mode persistence and policy helpers."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any


def _repo_root() -> Path:
    env_root = os.environ.get("OPENCLAW_REPO_ROOT", "").strip()
    if env_root:
        return Path(env_root).expanduser()
    return Path(__file__).resolve().parents[2]


def _default_system_path() -> Path:
    return Path("/opt/ai-ops-runner/artifacts/system/autonomy_mode.json")


def resolve_autonomy_mode_path() -> Path:
    explicit = os.environ.get("OPENCLAW_AUTONOMY_MODE_PATH", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    repo_root = _repo_root().resolve()
    if str(repo_root) == "/opt/ai-ops-runner" or str(repo_root).startswith("/opt/ai-ops-runner/"):
        return _default_system_path()
    return repo_root / "artifacts" / "system" / "autonomy_mode.json"


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def read_autonomy_mode() -> dict[str, Any]:
    candidates = [resolve_autonomy_mode_path(), _default_system_path()]
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate.resolve()) if candidate.exists() else str(candidate)
        if key in seen:
            continue
        seen.add(key)
        data = _read_json(candidate)
        if data:
            mode = "OFF" if str(data.get("mode")).upper() == "OFF" else "ON"
            return {
                "mode": mode,
                "updated_at": data.get("updated_at"),
                "updated_by": data.get("updated_by"),
                "path": str(candidate),
            }
    path = resolve_autonomy_mode_path()
    return {
        "mode": "ON",
        "updated_at": None,
        "updated_by": None,
        "path": str(path),
    }


def write_autonomy_mode(mode: str, updated_by: str) -> dict[str, Any]:
    normalized = "OFF" if str(mode).upper() == "OFF" else "ON"
    path = resolve_autonomy_mode_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "mode": normalized,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "updated_by": updated_by,
    }
    with NamedTemporaryFile("w", encoding="utf-8", dir=str(path.parent), delete=False) as tmp:
        json.dump(payload, tmp, indent=2)
        tmp.write("\n")
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)
    return {**payload, "path": str(path)}


def decide_action_policy(
    *,
    action: str,
    autonomy_mode: str,
    source: str = "autopilot",
    mutates_external: bool = True,
) -> dict[str, Any]:
    if source == "autopilot" and str(autonomy_mode).upper() == "OFF" and mutates_external:
        return {
            "decision": "SKIP_AUTONOMY_OFF",
            "allowed": False,
            "reason": "Autonomy mode is OFF; mutating automation is disabled.",
        }
    return {
        "decision": "AUTO",
        "allowed": True,
        "reason": "Autonomy mode allows this automated action.",
    }
