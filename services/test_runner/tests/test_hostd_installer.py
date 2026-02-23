"""Lightweight tests for hostd installer (venv ExecStart, ensure_hostd_venv_playwright.sh, watchdog)."""

from __future__ import annotations

import os

import pytest

REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")
)
INSTALL_SCRIPT = os.path.join(REPO_ROOT, "ops", "install_openclaw_hostd.sh")
ENSURE_PLAYWRIGHT_SCRIPT = os.path.join(
    REPO_ROOT, "ops", "scripts", "ensure_hostd_venv_playwright.sh"
)
HOSTD_WATCHDOG_SCRIPT = os.path.join(REPO_ROOT, "ops", "hostd_watchdog.sh")
HOSTD_WATCHDOG_SERVICE = os.path.join(
    REPO_ROOT, "ops", "systemd", "openclaw-hostd-watchdog.service"
)
HOSTD_WATCHDOG_TIMER = os.path.join(
    REPO_ROOT, "ops", "systemd", "openclaw-hostd-watchdog.timer"
)


def test_ensure_hostd_venv_playwright_exists():
    """ensure_hostd_venv_playwright.sh must exist in repo."""
    assert os.path.isfile(ENSURE_PLAYWRIGHT_SCRIPT), (
        f"ensure_hostd_venv_playwright.sh not found at {ENSURE_PLAYWRIGHT_SCRIPT}"
    )


def test_install_openclaw_hostd_uses_venv_execstart():
    """Hostd installer must use venv python in ExecStart."""
    with open(INSTALL_SCRIPT) as f:
        content = f.read()
    assert ".venv-hostd/bin/python" in content, (
        "install_openclaw_hostd.sh must contain .venv-hostd/bin/python in ExecStart"
    )


def test_hostd_watchdog_files_exist():
    """hostd watchdog script and systemd units must exist."""
    assert os.path.isfile(HOSTD_WATCHDOG_SCRIPT), (
        f"hostd_watchdog.sh not found at {HOSTD_WATCHDOG_SCRIPT}"
    )
    assert os.path.isfile(HOSTD_WATCHDOG_SERVICE), (
        f"openclaw-hostd-watchdog.service not found at {HOSTD_WATCHDOG_SERVICE}"
    )
    assert os.path.isfile(HOSTD_WATCHDOG_TIMER), (
        f"openclaw-hostd-watchdog.timer not found at {HOSTD_WATCHDOG_TIMER}"
    )


def test_install_openclaw_hostd_enables_hostd_watchdog_timer():
    """Installer must enable hostd watchdog timer when units exist."""
    with open(INSTALL_SCRIPT) as f:
        content = f.read()
    assert "openclaw-hostd-watchdog.timer" in content, (
        "install_openclaw_hostd.sh must enable openclaw-hostd-watchdog.timer"
    )
    assert "systemctl enable --now openclaw-hostd-watchdog.timer" in content or (
        "enable" in content and "openclaw-hostd-watchdog" in content
    ), "installer must enable hostd watchdog timer"


def test_hostd_watchdog_no_secrets_in_script():
    """hostd_watchdog.sh must not log secrets (logger only, no raw env)."""
    with open(HOSTD_WATCHDOG_SCRIPT) as f:
        content = f.read()
    # Script must not log env vars or keys that could contain secrets
    assert "logger" in content or "echo" in content, "watchdog must log via logger"
    assert "sk-" not in content and "tskey-" not in content, (
        "hostd_watchdog.sh must not contain key patterns"
    )
