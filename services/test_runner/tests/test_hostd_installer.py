"""Lightweight tests for hostd installer (venv ExecStart, ensure_hostd_venv_playwright.sh)."""

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
