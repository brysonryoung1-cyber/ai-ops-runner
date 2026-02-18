"""Tests for tools.backtest_gate — fail-closed enforcement."""
from __future__ import annotations

import os
import pytest
from tools.backtest_gate import check_backtest_only_gate


class TestBacktestOnlyFlag:
    def test_false_fails(self):
        r = check_backtest_only_gate(topk_backtest_only=False)
        assert not r.passed
        assert r.error_class == "BACKTEST_ONLY_REQUIRED"

    def test_none_fails(self):
        r = check_backtest_only_gate(topk_backtest_only=None)
        assert not r.passed


class TestEnvVar:
    def test_missing_env_fails(self, monkeypatch):
        monkeypatch.delenv("BACKTEST_ONLY", raising=False)
        r = check_backtest_only_gate(topk_backtest_only=True)
        assert not r.passed
        assert r.error_class == "BACKTEST_ONLY_ENV_MISSING"

    def test_env_true_passes(self, monkeypatch):
        monkeypatch.setenv("BACKTEST_ONLY", "true")
        r = check_backtest_only_gate(topk_backtest_only=True)
        assert r.passed

    def test_env_TRUE_passes(self, monkeypatch):
        monkeypatch.setenv("BACKTEST_ONLY", "TRUE")
        r = check_backtest_only_gate(topk_backtest_only=True)
        assert r.passed

    def test_env_false_fails(self, monkeypatch):
        monkeypatch.setenv("BACKTEST_ONLY", "false")
        r = check_backtest_only_gate(topk_backtest_only=True)
        assert not r.passed


class TestNt8ConnectionChecks:
    def test_simulated_connection_passes(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKTEST_ONLY", "true")
        conn = tmp_path / "connections.xml"
        conn.write_text("<Connections><Connection>Simulated Data Feed</Connection></Connections>")
        r = check_backtest_only_gate(topk_backtest_only=True, nt8_user_dir=str(tmp_path))
        assert r.passed

    def test_live_connection_fails(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKTEST_ONLY", "true")
        conn = tmp_path / "connections.xml"
        conn.write_text("<Connections><Connection>CQG Trading</Connection></Connections>")
        r = check_backtest_only_gate(topk_backtest_only=True, nt8_user_dir=str(tmp_path))
        assert not r.passed
        assert r.error_class == "LIVE_CONNECTIONS_DETECTED"

    def test_missing_dir_fails_closed(self, monkeypatch):
        monkeypatch.setenv("BACKTEST_ONLY", "true")
        r = check_backtest_only_gate(topk_backtest_only=True, nt8_user_dir="/nonexistent")
        assert not r.passed
        assert r.error_class == "LIVE_CONNECTIONS_UNKNOWN"

    def test_no_nt8_dir_skipped(self, monkeypatch):
        monkeypatch.setenv("BACKTEST_ONLY", "true")
        r = check_backtest_only_gate(topk_backtest_only=True, nt8_user_dir=None)
        assert r.passed

    def test_mixed_live_and_simulated_fails(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKTEST_ONLY", "true")
        conn = tmp_path / "connections.xml"
        conn.write_text(
            "<Connections>\n"
            "  <Connection>Simulated Data Feed</Connection>\n"
            "  <Connection>Rithmic Paper Trading</Connection>\n"
            "</Connections>"
        )
        r = check_backtest_only_gate(topk_backtest_only=True, nt8_user_dir=str(tmp_path))
        assert not r.passed
        assert r.error_class == "LIVE_CONNECTIONS_DETECTED"

    def test_nt8_dir_exists_but_no_connections_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKTEST_ONLY", "true")
        r = check_backtest_only_gate(topk_backtest_only=True, nt8_user_dir=str(tmp_path))
        assert not r.passed
        assert r.error_class == "LIVE_CONNECTIONS_UNKNOWN"

    def test_playback_only_passes(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKTEST_ONLY", "true")
        conn = tmp_path / "connections.xml"
        conn.write_text("<Connections><Connection>Playback Connection</Connection></Connections>")
        r = check_backtest_only_gate(topk_backtest_only=True, nt8_user_dir=str(tmp_path))
        assert r.passed
