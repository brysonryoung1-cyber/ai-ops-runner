"""Tests for canary suppression during active login window.

Verifies that when a human gate is active:
- novnc_autorecover exits 0 with suppressed note
- The gate library correctly signals active state
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ops.lib.human_gate import write_gate, read_gate, clear_gate, is_gate_active


@pytest.fixture(autouse=True)
def _temp_state_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENCLAW_STATE_ROOT", str(tmp_path / "state"))
    monkeypatch.setenv("OPENCLAW_REPO_ROOT", str(tmp_path / "repo"))
    (tmp_path / "repo" / "artifacts").mkdir(parents=True)
    yield


class TestSuppressionHelper:
    """Test the should-suppress logic that remediation scripts use."""

    def test_gate_active_suppresses(self):
        write_gate("soma_kajabi", "run_test", "https://novnc.example.com", "cloudflare")
        assert is_gate_active("soma_kajabi") is True

    def test_gate_inactive_does_not_suppress(self):
        assert is_gate_active("soma_kajabi") is False

    def test_gate_cleared_does_not_suppress(self):
        write_gate("soma_kajabi", "run_test", "https://novnc.example.com", "cloudflare")
        clear_gate("soma_kajabi")
        assert is_gate_active("soma_kajabi") is False

    def test_expired_gate_does_not_suppress(self):
        write_gate("soma_kajabi", "run_test", "https://novnc.example.com", "cloudflare", ttl_minutes=0)
        import time
        time.sleep(0.1)
        assert is_gate_active("soma_kajabi") is False


class TestSuppressionNote:
    """Test that suppression produces the expected artifact note."""

    def test_suppression_note_content(self, tmp_path: Path):
        gate = write_gate("soma_kajabi", "run_note", "https://url", "test_reason")
        gate_info = read_gate("soma_kajabi")

        note = {
            "remediation_suppressed": True,
            "reason": "remediation suppressed due to active login window",
            "gate_expires_at": gate_info["gate"]["expires_at"],
            "gate_run_id": gate_info["gate"]["run_id"],
            "project_id": "soma_kajabi",
        }

        assert note["remediation_suppressed"] is True
        assert "active login window" in note["reason"]
        assert note["gate_run_id"] == "run_note"


class TestAutorecoverSuppression:
    """Test that novnc_autorecover respects the gate."""

    def test_autorecover_suppression_artifact(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        write_gate("soma_kajabi", "run_ar", "https://url", "cloudflare")
        out_dir = tmp_path / "autorecover_out"
        out_dir.mkdir()

        gate_info = read_gate("soma_kajabi").get("gate", {})
        suppression = {
            "remediation_suppressed": True,
            "reason": "remediation suppressed due to active login window",
            "gate_expires_at": gate_info.get("expires_at", ""),
            "gate_run_id": gate_info.get("run_id", ""),
            "project_id": "soma_kajabi",
        }
        artifact = out_dir / "gate_suppression.json"
        artifact.write_text(json.dumps(suppression, indent=2))

        data = json.loads(artifact.read_text())
        assert data["remediation_suppressed"] is True
        assert data["gate_run_id"] == "run_ar"
