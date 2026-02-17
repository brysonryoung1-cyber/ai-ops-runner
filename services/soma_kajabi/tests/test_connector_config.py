"""Tests for connector config validation and readiness checks."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def test_config_validation_valid():
    """Valid config passes validation."""
    from services.soma_kajabi.connector_config import validate_config
    cfg = {
        "kajabi": {"mode": "session_token", "base_url": "https://app.kajabi.com"},
        "gmail": {"mode": "imap", "query": "from:(a) has:attachment"},
        "artifacts": {"base_dir": "artifacts/soma_kajabi/phase0"},
    }
    valid, err = validate_config(cfg)
    assert valid
    assert err is None


def test_config_validation_invalid_mode():
    """Invalid mode fails validation."""
    from services.soma_kajabi.connector_config import validate_config
    cfg = {
        "kajabi": {"mode": "invalid", "base_url": "https://app.kajabi.com"},
        "gmail": {"mode": "imap", "query": "x"},
        "artifacts": {"base_dir": "x"},
    }
    valid, err = validate_config(cfg)
    assert not valid
    assert "kajabi.mode" in err or "invalid" in err


def test_phase0_connector_not_configured_when_manual():
    """Phase 0 returns CONNECTOR_NOT_CONFIGURED when connectors not ready."""
    root = _repo_root()
    env = {"OPENCLAW_REPO_ROOT": str(root)}
    import os
    import subprocess
    r = subprocess.run(
        ["python3", "-m", "services.soma_kajabi.phase0_runner"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, **env},
    )
    out = r.stdout.strip()
    lines = [l for l in out.split("\n") if l.strip().startswith("{")]
    parsed = json.loads(lines[-1]) if lines else {}
    assert parsed.get("error_class") == "CONNECTOR_NOT_CONFIGURED"
    assert parsed.get("ok") is False


def test_storage_state_present_is_kajabi_ready():
    """When kajabi.mode is storage_state and file exists with _kjb_session cookie, is_kajabi_ready returns True."""
    from services.soma_kajabi.connector_config import is_kajabi_ready
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({
            "cookies": [{"name": "_kjb_session", "value": "secret-session-value", "domain": ".kajabi.com"}],
            "origins": [],
        }, f)
        path = f.name
    try:
        cfg = {
            "kajabi": {
                "mode": "storage_state",
                "base_url": "https://app.kajabi.com",
                "storage_state_secret_ref": path,
            },
            "gmail": {"mode": "imap", "query": "x"},
            "artifacts": {"base_dir": "artifacts/soma_kajabi/phase0"},
        }
        ready, reason = is_kajabi_ready(cfg)
        assert ready is True
        assert "storage_state" in reason.lower() or "present" in reason.lower()
    finally:
        Path(path).unlink(missing_ok=True)
