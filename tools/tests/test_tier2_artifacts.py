"""Tests for tools.tier2_artifacts — deterministic artifact writer."""
from __future__ import annotations

import csv
import json
from tools.tier2_artifacts import Tier2Artifacts, RESULTS_COLUMNS


class TestEnsureDirs:
    def test_creates_tree(self, tmp_path):
        a = Tier2Artifacts(tmp_path / "out", "cand-001")
        a.ensure_dirs()
        assert (tmp_path / "out" / "tier2").is_dir()
        assert (tmp_path / "out" / "tier2" / "raw_exports").is_dir()

    def test_idempotent(self, tmp_path):
        a = Tier2Artifacts(tmp_path / "out", "cand-001")
        a.ensure_dirs()
        a.ensure_dirs()
        assert (tmp_path / "out" / "tier2").is_dir()


class TestResultsCsv:
    def test_header_only_stub(self, tmp_path):
        a = Tier2Artifacts(tmp_path / "out", "cand-001")
        a.ensure_dirs()
        p = a.write_results_csv()
        rows = list(csv.DictReader(p.open()))
        assert len(rows) == 0
        assert p.read_text().startswith("candidate_id,")

    def test_with_rows(self, tmp_path):
        a = Tier2Artifacts(tmp_path / "out", "cand-001")
        a.ensure_dirs()
        row = {c: "0" for c in RESULTS_COLUMNS}
        row["candidate_id"] = "cand-001"
        row["pnl"] = "1234.56"
        a.write_results_csv([row])
        rows = list(csv.DictReader((a.tier2_dir / "results.csv").open()))
        assert len(rows) == 1
        assert rows[0]["candidate_id"] == "cand-001"
        assert rows[0]["pnl"] == "1234.56"


class TestSummaryJson:
    def test_pass_verdict(self, tmp_path):
        a = Tier2Artifacts(tmp_path / "out", "cand-001")
        a.ensure_dirs()
        a.write_summary("PASS", ["all clear"], best_candidate="cand-001")
        doc = json.loads((a.tier2_dir / "summary.json").read_text())
        assert doc["verdict"] == "PASS"
        assert doc["best_candidate"] == "cand-001"
        assert "cand-001" == doc["candidate_id"]

    def test_fail_verdict(self, tmp_path):
        a = Tier2Artifacts(tmp_path / "out", "cand-001")
        a.ensure_dirs()
        a.write_summary("FAIL", ["bad sharpe"], extra={"error_class": "METRICS_BELOW_THRESHOLD"})
        doc = json.loads((a.tier2_dir / "summary.json").read_text())
        assert doc["verdict"] == "FAIL"
        assert doc["error_class"] == "METRICS_BELOW_THRESHOLD"


class TestDoneJson:
    def test_done_marker(self, tmp_path):
        a = Tier2Artifacts(tmp_path / "out", "cand-001")
        a.ensure_dirs()
        a.write_done(0, "PASS")
        doc = json.loads((a.tier2_dir / "done.json").read_text())
        assert doc["done"] is True
        assert doc["exit_code"] == 0
        assert doc["status"] == "PASS"
        assert doc["run_id"].startswith("t2-cand-001-")


class TestStubArtifacts:
    def test_full_skeleton(self, tmp_path):
        a = Tier2Artifacts(tmp_path / "out", "cand-001")
        a.write_stub_artifacts("NT8_AUTOMATION_NOT_IMPLEMENTED")
        t2 = tmp_path / "out" / "tier2"
        assert (t2 / "results.csv").exists()
        assert (t2 / "summary.json").exists()
        assert (t2 / "done.json").exists()
        assert (t2 / "raw_exports").is_dir()
        done = json.loads((t2 / "done.json").read_text())
        assert done["exit_code"] == 3


class TestIdempotency:
    def test_run_id_stable(self, tmp_path):
        a1 = Tier2Artifacts(tmp_path / "out", "cand-001")
        a2 = Tier2Artifacts(tmp_path / "out", "cand-001")
        assert a1.run_id == a2.run_id

    def test_run_id_varies_by_candidate(self, tmp_path):
        a1 = Tier2Artifacts(tmp_path / "out", "cand-001")
        a2 = Tier2Artifacts(tmp_path / "out", "cand-002")
        assert a1.run_id != a2.run_id


class TestCopyRawExports:
    def test_copies_files(self, tmp_path):
        src = tmp_path / "raw"
        src.mkdir()
        (src / "trade_log.csv").write_text("a,b\n1,2\n")
        a = Tier2Artifacts(tmp_path / "out", "cand-001")
        a.ensure_dirs()
        a.copy_raw_exports(src)
        assert (a.tier2_dir / "raw_exports" / "trade_log.csv").read_text() == "a,b\n1,2\n"

    def test_skips_missing_src(self, tmp_path):
        a = Tier2Artifacts(tmp_path / "out", "cand-001")
        a.ensure_dirs()
        a.copy_raw_exports(tmp_path / "nope")
