"""Load and validate pred_markets project config. Fail-closed; no secrets."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

try:
    import jsonschema
except ImportError:
    jsonschema = None  # type: ignore[assignment]


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


def get_kill_switch(root: Path) -> bool:
    state = load_project_state(root)
    proj = state.get("projects") or {}
    pm = proj.get("pred_markets") or {}
    return bool(pm.get("kill_switch", True))


def load_pred_markets_config(root: Path) -> tuple[dict[str, Any] | None, str | None]:
    """Load config/projects/pred_markets.json and validate against schema.
    Returns (config_dict, None) on success or (None, error_message) on failure.
    """
    config_path = root / "config" / "projects" / "pred_markets.json"
    schema_path = root / "config" / "projects" / "pred_markets.schema.json"
    if not config_path.exists():
        return None, "config/projects/pred_markets.json missing"
    if not schema_path.exists():
        return None, "config/projects/pred_markets.schema.json missing"
    try:
        config = json.loads(config_path.read_text())
    except json.JSONDecodeError as e:
        return None, f"Invalid JSON: {e}"
    try:
        schema = json.loads(schema_path.read_text())
    except json.JSONDecodeError as e:
        return None, f"Invalid schema JSON: {e}"
    if jsonschema is not None:
        try:
            jsonschema.validate(instance=config, schema=schema)
        except jsonschema.ValidationError as e:
            return None, f"Schema validation failed: {e}"
    else:
        req = schema.get("required") or []
        for key in req:
            if key not in config:
                return None, f"Schema validation failed: missing required field '{key}'"
    return config, None
