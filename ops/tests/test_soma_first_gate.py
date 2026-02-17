"""Hermetic tests for Soma-first gate: orb.backtest.* blocked until baseline PASS and gate unlocked."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def test_gate_blocked_when_allow_orb_backtests_false():
    """When gates.allow_orb_backtests is false, orb backtest is not allowed."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "config").mkdir()
        state = {
            "gates": {"allow_orb_backtests": False},
            "projects": {"soma_kajabi": {"phase0_baseline_status": "PASS"}},
        }
        (root / "config" / "project_state.json").write_text(json.dumps(state))
        env = {**os.environ, "OPENCLAW_REPO_ROOT": str(root)}
        # Import after setting env so ROOT_DIR is correct
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "openclaw_hostd",
            Path(__file__).resolve().parents[1] / "openclaw_hostd.py",
        )
        assert spec and spec.loader
        hostd = importlib.util.module_from_spec(spec)
        os.environ["OPENCLAW_REPO_ROOT"] = str(root)
        spec.loader.exec_module(hostd)
        allowed, reason = hostd.is_orb_backtest_allowed()
        assert allowed is False
        assert "allow_orb_backtests" in reason


def test_gate_blocked_when_baseline_not_pass():
    """When phase0_baseline_status is not PASS, orb backtest is not allowed even if gate true."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "config").mkdir()
        state = {
            "gates": {"allow_orb_backtests": True},
            "projects": {"soma_kajabi": {"phase0_baseline_status": "UNKNOWN"}},
        }
        (root / "config" / "project_state.json").write_text(json.dumps(state))
        env = {**os.environ, "OPENCLAW_REPO_ROOT": str(root)}
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "openclaw_hostd",
            Path(__file__).resolve().parents[1] / "openclaw_hostd.py",
        )
        assert spec and spec.loader
        hostd = importlib.util.module_from_spec(spec)
        os.environ["OPENCLAW_REPO_ROOT"] = str(root)
        spec.loader.exec_module(hostd)
        allowed, reason = hostd.is_orb_backtest_allowed()
        assert allowed is False
        assert "PASS" in reason or "phase0" in reason.lower()


def test_gate_allowed_when_baseline_pass_and_gate_true():
    """When phase0_baseline_status is PASS and allow_orb_backtests is true, allowed."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "config").mkdir()
        state = {
            "gates": {"allow_orb_backtests": True},
            "projects": {"soma_kajabi": {"phase0_baseline_status": "PASS"}},
        }
        (root / "config" / "project_state.json").write_text(json.dumps(state))
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "openclaw_hostd",
            Path(__file__).resolve().parents[1] / "openclaw_hostd.py",
        )
        assert spec and spec.loader
        hostd = importlib.util.module_from_spec(spec)
        os.environ["OPENCLAW_REPO_ROOT"] = str(root)
        spec.loader.exec_module(hostd)
        allowed, reason = hostd.is_orb_backtest_allowed()
        assert allowed is True
        assert reason == ""
