"""Unit tests for noVNC URL/readiness wiring contracts."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "ops" / "scripts"
sys.path.insert(0, str(SCRIPTS))


def test_novnc_ready_adapter_writes_pointer_artifact(tmp_path, monkeypatch):
    """novnc_ready adapter should persist readiness artifact pointer for callers."""
    import novnc_ready as nr
    from ops.lib.novnc_readiness import ReadinessOutcome

    fake = ReadinessOutcome(
        ok=False,
        result="FAIL",
        run_id="r1",
        mode="deep",
        error_class="NOVNC_NOT_READY",
        novnc_url="https://test.ts.net/novnc/vnc.html?autoconnect=1&path=/websockify",
        artifact_dir="artifacts/novnc_readiness/r1",
        readiness_artifact_dir="artifacts/novnc_readiness/r1",
        ws_stability_local="failed",
        ws_stability_tailnet="failed",
        attempts=6,
        elapsed_sec=120.0,
        journal_artifact="artifacts/novnc_readiness/r1/journal_tail.txt",
    )
    monkeypatch.setattr(nr, "_ensure_convergent", lambda **kwargs: fake)

    ready, url, err_class, journal = nr.ensure_novnc_ready(tmp_path, "run_123")
    assert ready is False
    assert url.startswith("https://")
    assert err_class == "NOVNC_NOT_READY"
    assert journal and journal.endswith("journal_tail.txt")

    pointer = tmp_path / "novnc_readiness_pointer.json"
    assert pointer.exists()
    data = json.loads(pointer.read_text())
    assert data["novnc_readiness_artifact_dir"] == "artifacts/novnc_readiness/r1"


def test_novnc_url_must_not_be_localhost() -> None:
    """Canonical URL builder must never emit localhost for operator-facing links."""
    import novnc_ready as nr

    url = nr._build_canonical_url("test.ts.net")
    assert url.startswith("https://test.ts.net/novnc/vnc.html?")
    assert "127.0.0.1" not in url
    assert "localhost" not in url


def test_doctor_uses_convergent_readiness_module() -> None:
    """Doctor wrapper should delegate to ops.lib.novnc_readiness."""
    doctor = REPO_ROOT / "ops" / "openclaw_novnc_doctor.sh"
    content = doctor.read_text()
    assert "python3 -m ops.lib.novnc_readiness" in content
    assert "--emit-artifacts" in content


def test_readiness_module_probe_contract_markers() -> None:
    """Readiness module should encode required probe paths and backoff schedule."""
    readiness = REPO_ROOT / "ops" / "lib" / "novnc_readiness.py"
    content = readiness.read_text()
    assert "/novnc/vnc.html" in content
    assert "/websockify" in content
    assert "tcp_backend_vnc" in content
    assert "BACKOFF_DEEP = (2, 4, 8, 16, 32" in content
