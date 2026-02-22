"""Soma Kajabi connector config loader with schema validation.

Fail-closed: CONFIG_INVALID when config missing or invalid.
No secrets in output; only masked fingerprints when logging.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REPO_ROOT_ENV = "OPENCLAW_REPO_ROOT"
SECRETS_BASE = Path("/etc/ai-ops-runner/secrets")
SOMA_KAJABI_SECRETS = SECRETS_BASE / "soma_kajabi"
KAJABI_STORAGE_STATE_PATH = SOMA_KAJABI_SECRETS / "kajabi_storage_state.json"
KAJABI_PRODUCTS_PATH = SOMA_KAJABI_SECRETS / "kajabi_products.json"
GMAIL_OAUTH_PATH = SOMA_KAJABI_SECRETS / "gmail_oauth.json"
GMAIL_STATE_PATH = SOMA_KAJABI_SECRETS / "gmail_state.json"


def _repo_root() -> Path:
    env_root = __import__("os").environ.get(REPO_ROOT_ENV)
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


def _load_schema(root: Path) -> dict[str, Any] | None:
    schema_path = root / "config" / "projects" / "soma_kajabi.schema.json"
    if not schema_path.exists():
        return None
    try:
        return json.loads(schema_path.read_text())
    except Exception:
        return None


def _validate_required(obj: dict, path: str, required: list[str]) -> list[str]:
    """Return list of missing required keys."""
    missing = []
    for k in required:
        if k not in obj or obj[k] is None:
            missing.append(f"{path}.{k}")
    return missing


def validate_config(cfg: dict[str, Any]) -> tuple[bool, str | None]:
    """Validate config structure. Return (valid, error_message)."""
    for key in ("kajabi", "gmail", "artifacts"):
        if key not in cfg or not isinstance(cfg[key], dict):
            return False, f"Missing or invalid top-level key: {key}"
    kajabi = cfg["kajabi"]
    gmail = cfg["gmail"]
    artifacts = cfg["artifacts"]
    missing = []
    missing.extend(_validate_required(kajabi, "kajabi", ["mode", "base_url"]))
    missing.extend(_validate_required(gmail, "gmail", ["mode", "query"]))
    missing.extend(_validate_required(artifacts, "artifacts", ["base_dir"]))
    if missing:
        return False, f"Missing required keys: {', '.join(missing)}"
    valid_modes_kajabi = ("manual", "storage_state", "session_token")
    if kajabi.get("mode") not in valid_modes_kajabi:
        return False, f"kajabi.mode must be one of {valid_modes_kajabi}"
    valid_modes_gmail = ("manual", "oauth", "imap")
    if gmail.get("mode") not in valid_modes_gmail:
        return False, f"gmail.mode must be one of {valid_modes_gmail}"
    return True, None


def load_soma_kajabi_config(root: Path) -> tuple[dict[str, Any], str | None]:
    """Load and validate config. Return (config, error_class).

    error_class is CONFIG_INVALID when config missing/invalid, else None.
    """
    path = root / "config" / "projects" / "soma_kajabi.json"
    if not path.exists():
        return (
            _default_config(),
            "CONFIG_INVALID",
        )
    try:
        cfg = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        return (_default_config(), f"CONFIG_INVALID: {e!s}")

    # Merge legacy top-level keys for backward compat
    cfg.setdefault("kajabi", {})
    cfg.setdefault("gmail", {})
    cfg.setdefault("artifacts", {})
    if "kajabi_capture_mode" in cfg:
        mode_map = {"manual": "manual", "playwright": "storage_state"}
        cfg["kajabi"]["mode"] = mode_map.get(cfg["kajabi_capture_mode"], cfg["kajabi"].get("mode", "manual"))
    if "gmail_capture_mode" in cfg:
        mode_map = {"manual": "manual", "oauth": "oauth"}
        cfg["gmail"]["mode"] = mode_map.get(cfg["gmail_capture_mode"], cfg["gmail"].get("mode", "manual"))
    cfg["kajabi"].setdefault("mode", "manual")
    cfg["kajabi"].setdefault("base_url", "https://app.kajabi.com")
    cfg["gmail"].setdefault("mode", "manual")
    cfg["gmail"].setdefault("query", "from:(Zane McCourtney) has:attachment")
    cfg["artifacts"].setdefault("base_dir", "artifacts/soma_kajabi/phase0")

    valid, err = validate_config(cfg)
    if not valid:
        return (cfg, f"CONFIG_INVALID: {err}")
    return (cfg, None)


def _default_config() -> dict[str, Any]:
    return {
        "kajabi": {"mode": "manual", "base_url": "https://app.kajabi.com"},
        "gmail": {"mode": "manual", "query": "from:(Zane McCourtney) has:attachment"},
        "artifacts": {"base_dir": "artifacts/soma_kajabi/phase0"},
    }


def is_kajabi_ready(cfg: dict[str, Any]) -> tuple[bool, str]:
    """Check Kajabi connector readiness. Return (ready, reason)."""
    mode = cfg.get("kajabi", {}).get("mode", "manual")
    if mode == "manual":
        return False, "Kajabi mode is manual; run bootstrap to configure"
    if mode == "storage_state":
        path_str = cfg.get("kajabi", {}).get("storage_state_secret_ref") or str(KAJABI_STORAGE_STATE_PATH)
        path = Path(path_str)
        if path.exists() and path.stat().st_size > 0:
            try:
                data = json.loads(path.read_text())
                if isinstance(data, dict):
                    return True, "storage_state present"
            except Exception:
                pass
        return False, f"storage_state not found or invalid at {path_str}"
    if mode == "session_token":
        try:
            from services.soma_kajabi_sync.config import load_secret
            token = load_secret("KAJABI_SESSION_TOKEN", required=False)
            if token:
                return True, "session_token present"
        except Exception:
            pass
        return False, "Kajabi session token not configured"
    return False, f"unknown kajabi mode: {mode}"


def is_gmail_ready(cfg: dict[str, Any]) -> tuple[bool, str]:
    """Check Gmail connector readiness. Return (ready, reason)."""
    mode = cfg.get("gmail", {}).get("mode", "manual")
    if mode == "manual":
        return False, "Gmail mode is manual; run connect to configure"
    if mode == "imap":
        try:
            from services.soma_kajabi_sync.config import load_secret
            user = load_secret("GMAIL_USER", required=False)
            pwd = load_secret("GMAIL_APP_PASSWORD", required=False)
            if user and pwd:
                return True, "imap credentials present"
        except Exception:
            pass
        return False, "Gmail credentials not configured"
    if mode == "oauth":
        path_str = cfg.get("gmail", {}).get("auth_secret_ref") or str(GMAIL_OAUTH_PATH)
        path = Path(path_str)
        if path.exists() and path.stat().st_size > 0:
            try:
                data = json.loads(path.read_text())
                if isinstance(data, dict) and data.get("refresh_token"):
                    return True, "oauth refresh_token present"
            except Exception:
                pass
        return False, f"oauth token not found at {path_str}"
    return False, f"unknown gmail mode: {mode}"


def connectors_status(root: Path) -> dict[str, Any]:
    """Return connector status for doctor/HQ. No secrets."""
    cfg, err_class = load_soma_kajabi_config(root)
    if err_class:
        return {
            "config_valid": False,
            "error_class": err_class,
            "kajabi": "unknown",
            "gmail": "unknown",
        }
    kajabi_ok, kajabi_reason = is_kajabi_ready(cfg)
    gmail_ok, gmail_reason = is_gmail_ready(cfg)
    return {
        "config_valid": True,
        "kajabi": "connected" if kajabi_ok else "not_connected",
        "kajabi_reason": kajabi_reason,
        "gmail": "connected" if gmail_ok else "not_connected",
        "gmail_reason": gmail_reason,
    }
