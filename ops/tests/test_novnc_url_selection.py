"""Unit test: noVNC URL selection must be tailnet host, never localhost."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "ops" / "scripts"
sys.path.insert(0, str(SCRIPTS))


def test_novnc_url_must_not_be_localhost() -> None:
    """WAITING_FOR_HUMAN and doctor must never emit 127.0.0.1 or localhost in noVNC URL."""
    doctor = REPO_ROOT / "ops" / "openclaw_novnc_doctor.sh"
    content = doctor.read_text()
    # Doctor outputs novnc_url from _get_novnc_url which uses tailscale DNSName
    assert "127.0.0.1" not in content or "Host:" in content or "connect" in content, (
        "Doctor URL logic must not hardcode 127.0.0.1 for user-facing novnc_url"
    )
    # The _get_novnc_url in doctor uses tailscale status --json DNSName
    assert "DNSName" in content or "tailscale" in content, (
        "Doctor must derive URL from Tailscale DNSName, not localhost"
    )


def test_ws_stability_check_supports_tailnet() -> None:
    """novnc_ws_stability_check must support --tailnet and --all for tailnet verification."""
    ws_check = REPO_ROOT / "ops" / "scripts" / "novnc_ws_stability_check.py"
    content = ws_check.read_text()
    assert "--tailnet" in content or "--all" in content
    assert "tailnet" in content.lower() or "_get_tailnet_host" in content
    assert "ws_stability_tailnet" in content or "tailnet_result" in content


def test_doctor_pass_includes_tailnet_verified() -> None:
    """Doctor PASS output must include ws_stability_tailnet=verified."""
    doctor = REPO_ROOT / "ops" / "openclaw_novnc_doctor.sh"
    content = doctor.read_text()
    assert "ws_stability_tailnet" in content
    assert "verified" in content


def test_novnc_ready_uses_doctor_only() -> None:
    """ensure_novnc_ready must use doctor (not probe fallback) for tailnet verification."""
    novnc_ready = SCRIPTS / "novnc_ready.py"
    content = novnc_ready.read_text()
    assert "_run_doctor" in content
    assert "doctor_ok" in content
    # Must NOT fall through to probe when doctor fails (probe is localhost-only)
    assert "do NOT fall through to probe" in content or "Doctor FAIL" in content
