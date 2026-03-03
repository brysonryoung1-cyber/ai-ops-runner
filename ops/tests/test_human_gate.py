"""Tests for ops/lib/human_gate.py — write/read/clear/auto-expire."""

from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest import mock

import pytest

# Ensure repo root is on path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ops.lib.human_gate import (
    _DEFAULT_TTL_MINUTES,
    clear_gate,
    is_gate_active,
    read_gate,
    touch_gate,
    write_gate,
    write_gate_artifact,
)


@pytest.fixture(autouse=True)
def _temp_state_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENCLAW_STATE_ROOT", str(tmp_path / "state"))
    monkeypatch.setenv("OPENCLAW_REPO_ROOT", str(tmp_path / "repo"))
    (tmp_path / "repo" / "artifacts").mkdir(parents=True)
    yield


class TestWriteReadGate:
    def test_write_then_read_active(self):
        gate = write_gate("soma_kajabi", "run_123", "https://novnc.example.com", "cloudflare")
        assert gate["active"] is True
        assert gate["run_id"] == "run_123"
        assert gate["novnc_url"] == "https://novnc.example.com"
        assert gate["reason"] == "cloudflare"
        assert "expires_at" in gate

        result = read_gate("soma_kajabi")
        assert result["active"] is True
        assert result["gate"]["run_id"] == "run_123"

    def test_read_nonexistent_returns_inactive(self):
        result = read_gate("nonexistent_project")
        assert result["active"] is False
        assert result["gate"] is None

    def test_is_gate_active_true(self):
        write_gate("soma_kajabi", "run_456", "https://url", "reason")
        assert is_gate_active("soma_kajabi") is True

    def test_is_gate_active_false_when_no_gate(self):
        assert is_gate_active("soma_kajabi") is False


class TestClearGate:
    def test_clear_removes_gate(self):
        write_gate("soma_kajabi", "run_789", "https://url", "reason")
        assert is_gate_active("soma_kajabi") is True
        clear_gate("soma_kajabi")
        assert is_gate_active("soma_kajabi") is False

    def test_clear_nonexistent_no_error(self):
        clear_gate("nonexistent_project")  # should not raise


class TestAutoExpiry:
    def test_expired_gate_returns_inactive(self):
        gate = write_gate("soma_kajabi", "run_exp", "https://url", "reason", ttl_minutes=0)
        time.sleep(0.1)
        result = read_gate("soma_kajabi")
        assert result["active"] is False
        assert result["gate"] is None

    def test_not_yet_expired_returns_active(self):
        write_gate("soma_kajabi", "run_ttl", "https://url", "reason", ttl_minutes=60)
        result = read_gate("soma_kajabi")
        assert result["active"] is True


class TestWriteGateArtifact:
    def test_writes_human_gate_json(self):
        gate = write_gate("soma_kajabi", "run_art", "https://url", "reason")
        path = write_gate_artifact("soma_kajabi", "run_art", gate)

        assert path.exists()
        assert path.name == "HUMAN_GATE.json"
        data = json.loads(path.read_text())
        assert data["run_id"] == "run_art"
        assert data["novnc_url"] == "https://url"


class TestDefaultTTL:
    def test_default_ttl_is_35_minutes(self):
        assert _DEFAULT_TTL_MINUTES == 35

    def test_write_gate_uses_default_ttl(self):
        gate = write_gate("soma_kajabi", "run_ttl35", "https://url", "reason")
        assert gate["ttl_minutes"] == 35

    def test_env_override_ttl(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OPENCLAW_HUMAN_GATE_TTL_MINUTES", "45")
        gate = write_gate("soma_kajabi", "run_env_ttl", "https://url", "reason")
        assert gate["ttl_minutes"] == 45


class TestTouchGate:
    def test_touch_extends_expires_at(self):
        gate = write_gate("soma_kajabi", "run_touch", "https://url", "reason", ttl_minutes=5)
        original_expires = gate["expires_at"]
        time.sleep(0.1)
        result = touch_gate("soma_kajabi", ttl_minutes=60)
        assert result is True
        updated = read_gate("soma_kajabi")
        assert updated["active"] is True
        assert updated["gate"]["expires_at"] > original_expires

    def test_touch_nonexistent_returns_false(self):
        assert touch_gate("nonexistent_project") is False

    def test_touch_expired_returns_false(self):
        write_gate("soma_kajabi", "run_expired", "https://url", "reason", ttl_minutes=0)
        time.sleep(0.1)
        assert touch_gate("soma_kajabi") is False


class TestIdempotentOverwrite:
    def test_second_write_overwrites(self):
        write_gate("soma_kajabi", "run_1", "https://url1", "reason1")
        write_gate("soma_kajabi", "run_2", "https://url2", "reason2")
        result = read_gate("soma_kajabi")
        assert result["gate"]["run_id"] == "run_2"
        assert result["gate"]["novnc_url"] == "https://url2"
