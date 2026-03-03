"""Tests for kajabi_capture_interactive — noVNC stop gating."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_capture_interactive_only_stops_novnc_on_success():
    """On WAITING/ok=False, _stop_novnc_systemd is NOT called unconditionally."""
    script = REPO_ROOT / "ops" / "scripts" / "kajabi_capture_interactive.py"
    content = script.read_text()
    # Find call sites (indent + call), not the function definition
    lines = content.split("\n")
    call_lines = [
        (i, line) for i, line in enumerate(lines)
        if "_stop_novnc_systemd()" in line and "def " not in line
    ]
    assert len(call_lines) >= 1, "expected at least one _stop_novnc_systemd() call site"
    for line_no, line in call_lines:
        context = "\n".join(lines[max(0, line_no - 5):line_no + 1])
        assert "ok" in context, \
            f"_stop_novnc_systemd at line {line_no + 1} must be guarded by ok-check"


def test_capture_interactive_has_storage_state_resolver():
    """capture_interactive uses _resolve_storage_state_path for path unification."""
    script = REPO_ROOT / "ops" / "scripts" / "kajabi_capture_interactive.py"
    content = script.read_text()
    assert "_resolve_storage_state_path" in content
    assert "get_storage_state_path" in content
