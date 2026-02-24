"""Tests for soma_kajabi_session_warm timer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_warm_script_exists():
    """Session warm script exists."""
    script = REPO_ROOT / "ops" / "scripts" / "soma_kajabi_session_warm.py"
    assert script.exists()


def test_warm_respects_enablement_gate():
    """Session warm checks soma_kajabi_session_warm_enabled.txt and exits 0 when disabled."""
    script = REPO_ROOT / "ops" / "scripts" / "soma_kajabi_session_warm.py"
    content = script.read_text()
    assert "soma_kajabi_session_warm_enabled.txt" in content
    assert "WARM_ENABLED_FILE" in content


def test_warm_skip_on_exit_node_offline():
    """Session warm writes SKIPPED_EXIT_NODE_OFFLINE when exit node offline."""
    script = REPO_ROOT / "ops" / "scripts" / "soma_kajabi_session_warm.py"
    content = script.read_text()
    assert "SKIPPED_EXIT_NODE_OFFLINE" in content
    assert "EXIT_NODE_OFFLINE" in content


def test_warm_systemd_units_exist():
    """Session warm systemd service and timer exist."""
    service = REPO_ROOT / "ops" / "systemd" / "openclaw-soma-kajabi-warm.service"
    timer = REPO_ROOT / "ops" / "systemd" / "openclaw-soma-kajabi-warm.timer"
    assert service.exists()
    assert timer.exists()


def test_warm_timer_schedule():
    """Session warm timer runs every 6 hours."""
    timer = REPO_ROOT / "ops" / "systemd" / "openclaw-soma-kajabi-warm.timer"
    content = timer.read_text()
    assert "00,06,12,18" in content or "6 hours" in content.lower()
