"""Tests for soma_fix_and_retry action (Phase G)."""

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_soma_fix_and_retry_registered():
    """soma_fix_and_retry is in action_registry.json."""
    registry = REPO_ROOT / "config" / "action_registry.json"
    data = json.loads(registry.read_text())
    ids = [a["id"] for a in data["actions"]]
    assert "soma_fix_and_retry" in ids


def test_soma_fix_and_retry_script_exists():
    """soma_fix_and_retry.py exists and has main logic."""
    script = REPO_ROOT / "ops" / "scripts" / "soma_fix_and_retry.py"
    assert script.exists()
    content = script.read_text()
    assert "openclaw_novnc_doctor" in content
    assert "openclaw_novnc_shm_fix" in content
    assert "openclaw_novnc_restart" in content
    assert "soma_run_to_done" in content
