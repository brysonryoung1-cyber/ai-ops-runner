"""Tests for soma_kajabi_session_check action."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_session_check_action_registered():
    """Action soma_kajabi_session_check is in action_registry."""
    reg = REPO_ROOT / "config" / "action_registry.json"
    assert reg.exists()
    data = json.loads(reg.read_text())
    ids = [a["id"] for a in data["actions"]]
    assert "soma_kajabi_session_check" in ids


def test_session_check_script_has_profile_dir():
    """Session check uses persistent Chromium profile via launch_persistent_context."""
    script = REPO_ROOT / "ops" / "scripts" / "soma_kajabi_session_check.py"
    assert script.exists()
    content = script.read_text()
    assert "kajabi_chrome_profile" in content
    assert "profile_dir_used" in content
    assert "launch_persistent_context" in content


def test_session_check_produces_summary_artifacts():
    """Session check artifact structure includes SUMMARY.md, summary.json, screenshot.png, page_title.txt."""
    script = REPO_ROOT / "ops" / "scripts" / "soma_kajabi_session_check.py"
    content = script.read_text()
    assert "SUMMARY.md" in content
    assert "summary.json" in content
    assert "screenshot.png" in content
    assert "page_title.txt" in content


def test_session_check_docstring_no_secrets():
    """Session check docstring states no secrets."""
    script = REPO_ROOT / "ops" / "scripts" / "soma_kajabi_session_check.py"
    content = script.read_text()
    assert "No secrets" in content or "no secrets" in content


def test_session_check_calls_ensure_novnc_before_waiting_for_human():
    """On Cloudflare block, session_check calls ensure_novnc_ready (restart + probe) before WAITING_FOR_HUMAN."""
    script = REPO_ROOT / "ops" / "scripts" / "soma_kajabi_session_check.py"
    content = script.read_text()
    assert "ensure_novnc_ready" in content
    assert "WAITING_FOR_HUMAN" in content
    assert "noVNC READY" in content
    cloudflare_idx = content.find("if cloudflare or login_or_404:")
    assert cloudflare_idx >= 0
    ensure_idx = content.find("ensure_novnc_ready", cloudflare_idx)
    waiting_idx = content.find("WAITING_FOR_HUMAN", cloudflare_idx)
    assert ensure_idx >= 0, "ensure_novnc_ready must be in cloudflare path"
    assert waiting_idx >= 0, "WAITING_FOR_HUMAN must be in cloudflare path"
    assert ensure_idx < waiting_idx, "ensure_novnc_ready must be called before WAITING_FOR_HUMAN on cloudflare"


def test_session_check_guards_stop_novnc_with_gate():
    """When gate active, session_check does NOT call _stop_novnc_systemd."""
    script = REPO_ROOT / "ops" / "scripts" / "soma_kajabi_session_check.py"
    content = script.read_text()
    assert "is_gate_active" in content
    # Find the cleanup section (after "Cleanup noVNC" comment)
    cleanup_idx = content.find("# Cleanup noVNC")
    assert cleanup_idx >= 0, "Expected '# Cleanup noVNC' comment"
    cleanup_section = content[cleanup_idx:]
    assert "is_gate_active" in cleanup_section, \
        "is_gate_active check must be in the cleanup section"
    assert "not _gate_active" in cleanup_section, \
        "_stop_novnc_systemd must be conditional on gate not active"
