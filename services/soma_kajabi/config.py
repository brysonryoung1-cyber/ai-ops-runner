"""Project config loader for soma_kajabi. Fail-closed; no secrets in output."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def repo_root() -> Path:
    env_root = os.environ.get("OPENCLAW_REPO_ROOT")
    if env_root and Path(env_root).exists():
        return Path(env_root)
    cwd = Path.cwd()
    for _ in range(10):
        if (cwd / "config" / "project_state.json").exists():
            return cwd
        if cwd == cwd.parent:
            break
        cwd = cwd.parent
    return Path(env_root or "/opt/ai-ops-runner")


def load_project_state(root: Path) -> dict[str, Any]:
    path = root / "config" / "project_state.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def load_soma_kajabi_config(root: Path) -> dict[str, Any]:
    path = root / "config" / "projects" / "soma_kajabi.json"
    if not path.exists():
        return {
            "kajabi_capture_mode": "manual",
            "gmail_capture_mode": "manual",
        }
    try:
        return json.loads(path.read_text())
    except Exception:
        return {"kajabi_capture_mode": "manual", "gmail_capture_mode": "manual"}


def get_kill_switch(root: Path) -> bool:
    state = load_project_state(root)
    proj = state.get("projects") or {}
    sk = proj.get("soma_kajabi") or {}
    return sk.get("kill_switch", True)


def get_project_phase(root: Path) -> int:
    state = load_project_state(root)
    proj = state.get("projects") or {}
    sk = proj.get("soma_kajabi") or {}
    return int(sk.get("phase", 0))


def mask_fingerprint(val: str | None) -> str:
    """Mask for logs; never emit raw secrets."""
    if not val or len(val) <= 8:
        return "***"
    return f"{val[:4]}...{val[-4:]}"
