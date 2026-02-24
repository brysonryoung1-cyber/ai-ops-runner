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
    # Within cloudflare block: ensure_novnc_ready before WAITING_FOR_HUMAN (skip docstring at start)
    cloudflare_idx = content.find("if cloudflare:")
    assert cloudflare_idx >= 0
    # Find ensure_novnc_ready and WAITING_FOR_HUMAN after cloudflare block starts
    ensure_idx = content.find("ensure_novnc_ready", cloudflare_idx)
    waiting_idx = content.find("WAITING_FOR_HUMAN", cloudflare_idx)
    assert ensure_idx >= 0, "ensure_novnc_ready must be in cloudflare path"
    assert waiting_idx >= 0, "WAITING_FOR_HUMAN must be in cloudflare path"
    assert ensure_idx < waiting_idx, "ensure_novnc_ready must be called before WAITING_FOR_HUMAN on cloudflare"
