"""Unit tests for desired state load/validate."""
import json
import os
import tempfile
from pathlib import Path

import pytest

# Add repo root for imports
REPO_ROOT = Path(__file__).resolve().parents[2]
sys_path = list(__import__("sys").path)
if str(REPO_ROOT) not in sys_path:
    __import__("sys").path.insert(0, str(REPO_ROOT))

from ops.desired_state.load import load_desired_state, validate_desired_state


def test_load_desired_state():
    """Load from repo ops/desired_state/openclaw_desired_state.json."""
    state = load_desired_state(REPO_ROOT / "ops" / "desired_state" / "openclaw_desired_state.json")
    assert state["version"]
    assert state["tailscale_serve"]["single_root"] is True
    assert "8788" in state["tailscale_serve"]["target"]
    assert "/novnc/vnc.html" in state["novnc"]["http_path"]
    assert "/websockify" in state["novnc"]["ws_paths"]
    assert "/novnc/websockify" in state["novnc"]["ws_paths"]


def test_validate_rejects_missing_target():
    """Validation rejects target not targeting 8788."""
    data = {
        "version": "1",
        "tailscale_serve": {"single_root": True, "target": "http://127.0.0.1:9999"},
        "frontdoor": {"listen": "127.0.0.1:8788", "route_rules": []},
        "ports_services": {},
        "novnc": {
            "canonical_url_format": "https://<host>/novnc/vnc.html",
            "http_path": "/novnc/vnc.html",
            "ws_paths": ["/websockify", "/novnc/websockify"],
        },
        "invariants": {},
    }
    with pytest.raises(ValueError, match="8788"):
        validate_desired_state(data)


def test_validate_accepts_valid():
    """Validation accepts valid desired state."""
    data = {
        "version": "1",
        "tailscale_serve": {"single_root": True, "target": "http://127.0.0.1:8788"},
        "frontdoor": {"listen": "127.0.0.1:8788", "route_rules": []},
        "ports_services": {},
        "novnc": {
            "canonical_url_format": "https://<host>/novnc/vnc.html",
            "http_path": "/novnc/vnc.html",
            "ws_paths": ["/websockify", "/novnc/websockify"],
        },
        "invariants": {},
    }
    out = validate_desired_state(data)
    assert out == data


def test_get_canonical_novnc_url(monkeypatch):
    """Canonical noVNC URL format."""
    import ops.desired_state.load as load_mod
    monkeypatch.setattr(load_mod, "DESIRED_STATE_PATH", REPO_ROOT / "ops" / "desired_state" / "openclaw_desired_state.json")
    url = load_mod.get_canonical_novnc_url("aiops-1.tailc75c62.ts.net")
    assert "https://" in url
    assert "aiops-1.tailc75c62.ts.net" in url
    assert "/novnc/vnc.html" in url
    assert "path=/websockify" in url
