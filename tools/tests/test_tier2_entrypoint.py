"""Tests for tools.tier2_confirm_entrypoint — harness integration tests."""
from __future__ import annotations

import json
from pathlib import Path
from tools.tier2_confirm_entrypoint import main
from tools.tests.test_validate_topk import VALID_TOPK


def _write_topk(tmp_path: Path, data: dict | None = None) -> Path:
    p = tmp_path / "topk.json"
    p.write_text(json.dumps(data or VALID_TOPK))
    return p


class TestHappyPath:
    def test_stub_phase0(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKTEST_ONLY", "true")
        topk = _write_topk(tmp_path)
        out = tmp_path / "output"
        rc = main(["--topk", str(topk), "--output-dir", str(out), "--mode", "strategy_analyzer"])
        assert rc == 3
        t2 = out / "tier2"
        assert (t2 / "done.json").exists()
        assert (t2 / "summary.json").exists()
        assert (t2 / "results.csv").exists()
        done = json.loads((t2 / "done.json").read_text())
        assert done["status"] == "NT8_AUTOMATION_NOT_IMPLEMENTED"
        assert done["exit_code"] == 3

    def test_walk_forward_mode(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKTEST_ONLY", "true")
        topk = _write_topk(tmp_path)
        out = tmp_path / "output"
        rc = main(["--topk", str(topk), "--output-dir", str(out), "--mode", "walk_forward"])
        assert rc == 3


class TestValidationFailure:
    def test_invalid_topk_produces_artifacts(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKTEST_ONLY", "true")
        bad = {"candidate_id": "test"}
        topk = _write_topk(tmp_path, bad)
        out = tmp_path / "output"
        rc = main(["--topk", str(topk), "--output-dir", str(out), "--mode", "strategy_analyzer"])
        assert rc == 1
        done = json.loads((out / "tier2" / "done.json").read_text())
        assert done["status"] == "VALIDATION_FAILED"


class TestGateFailure:
    def test_no_env_var(self, tmp_path, monkeypatch):
        monkeypatch.delenv("BACKTEST_ONLY", raising=False)
        topk = _write_topk(tmp_path)
        out = tmp_path / "output"
        rc = main(["--topk", str(topk), "--output-dir", str(out), "--mode", "strategy_analyzer"])
        assert rc == 1
        done = json.loads((out / "tier2" / "done.json").read_text())
        assert done["status"] == "BACKTEST_ONLY_ENV_MISSING"


class TestIdempotency:
    def test_rerun_no_corruption(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKTEST_ONLY", "true")
        topk = _write_topk(tmp_path)
        out = tmp_path / "output"
        rc1 = main(["--topk", str(topk), "--output-dir", str(out), "--mode", "strategy_analyzer"])
        done1 = json.loads((out / "tier2" / "done.json").read_text())
        rc2 = main(["--topk", str(topk), "--output-dir", str(out), "--mode", "strategy_analyzer"])
        done2 = json.loads((out / "tier2" / "done.json").read_text())
        assert rc1 == rc2 == 3
        assert done1["run_id"] == done2["run_id"]

    def test_resumable_returns_existing_exit_code(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKTEST_ONLY", "true")
        topk = _write_topk(tmp_path)
        out = tmp_path / "output"
        (out / "tier2").mkdir(parents=True)
        (out / "tier2" / "raw_exports").mkdir()
        (out / "tier2" / "done.json").write_text(
            json.dumps({"done": True, "exit_code": 3, "status": "STUB", "run_id": "t2-x-0"})
        )
        rc = main(["--topk", str(topk), "--output-dir", str(out), "--mode", "strategy_analyzer"])
        assert rc == 3
