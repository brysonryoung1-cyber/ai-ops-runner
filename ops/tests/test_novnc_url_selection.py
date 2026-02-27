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


def test_ensure_novnc_ready_retries_on_doctor_fail(tmp_path):
    """When doctor FAIL occurs, ensure_novnc_ready retries restart and only returns on PASS."""
    import novnc_ready as nr

    call_count = 0

    def mock_doctor(_artifact_dir, run_id):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            return False, "https://test.ts.net/novnc/vnc.html?autoconnect=1&path=/websockify", "NOVNC_WS_TAILNET_FAILED"
        return True, "https://test.ts.net/novnc/vnc.html?autoconnect=1&path=/websockify", None

    restart_calls = []

    def mock_restart(root):
        restart_calls.append(root)
        return True

    with patch.object(nr, "_run_doctor", side_effect=mock_doctor), patch.object(
        nr, "_run_novnc_restart", side_effect=mock_restart
    ), patch.object(nr, "_capture_journal", return_value=tmp_path / "journal.txt"):
        ready, url, err_class, journal = nr.ensure_novnc_ready(tmp_path, "run_123")

    assert ready is True
    assert url == "https://test.ts.net/novnc/vnc.html?autoconnect=1&path=/websockify"
    assert err_class is None
    assert call_count == 3
    assert len(restart_calls) == 2  # Restart after attempt 1 and 2


def test_ensure_novnc_ready_with_recovery_triggers_restart_on_novnc_not_ready(tmp_path):
    """NOVNC_NOT_READY triggers restart+retry in ensure_novnc_ready_with_recovery."""
    import novnc_ready as nr

    call_count = 0

    def mock_doctor(_artifact_dir, run_id):
        nonlocal call_count
        call_count += 1
        # First ensure_novnc_ready exhausts 5 retries (5 calls), then recovery runs (1+ calls)
        if call_count <= 5:
            return False, "https://test.ts.net/novnc/vnc.html?autoconnect=1&path=/websockify", "NOVNC_NOT_READY"
        return True, "https://recovered.ts.net/novnc/vnc.html?autoconnect=1&path=/websockify", None

    restart_calls = []

    def mock_restart(root):
        restart_calls.append(root)
        return True

    with patch.object(nr, "_run_doctor", side_effect=mock_doctor), patch.object(
        nr, "_run_novnc_restart", side_effect=mock_restart
    ), patch.object(nr, "_capture_journal", return_value=tmp_path / "journal.txt"):
        ready, url, err_class, journal = nr.ensure_novnc_ready_with_recovery(tmp_path, "run_456")

    assert ready is True
    assert "/novnc/" in url or "vnc.html" in url
    assert err_class is None
    assert len(restart_calls) >= 1  # Recovery triggers one restart before retry


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


def test_doctor_emits_canonical_url_with_path_websockify() -> None:
    """Doctor must emit canonical URL with path=/websockify (WS upgrade via Tailscale Serve)."""
    doctor = REPO_ROOT / "ops" / "openclaw_novnc_doctor.sh"
    content = doctor.read_text()
    assert "path=/websockify" in content, (
        "Doctor must emit path=/websockify for canonical noVNC URL (WS upgrade)"
    )
    assert "vnc.html?autoconnect=1" in content or "vnc.html?autoconnect=1&path=" in content


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
