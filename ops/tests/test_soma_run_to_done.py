"""Unit tests for soma_run_to_done fail-closed mirror + acceptance gates."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_module():
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    spec = importlib.util.spec_from_file_location(
        "soma_run_to_done",
        REPO_ROOT / "ops" / "scripts" / "soma_run_to_done.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestWriteInitialProofFiles:
    """write_initial_proof_files must create PROOF.json and PRECHECK.json immediately."""

    def test_creates_both_files(self, tmp_path):
        mod = _load_module()
        out_dir = tmp_path / "run_dir"
        out_dir.mkdir()
        mod.write_initial_proof_files(
            out_dir,
            "test_run_123",
            console_run_id="20260305150000-c0de",
        )

        proof = json.loads((out_dir / "PROOF.json").read_text())
        precheck = json.loads((out_dir / "PRECHECK.json").read_text())

        assert proof["run_id"] == "test_run_123"
        assert proof["status"] == "RUNNING"
        assert proof["phase"] == "init"
        assert "started_at" in proof
        assert proof["project"] == "soma_kajabi"
        assert proof["console_run_id"] == "20260305150000-c0de"

        assert precheck["run_id"] == "test_run_123"
        assert precheck["status"] == "RUNNING"
        assert precheck["precheck"] == "pending"
        assert "started_at" in precheck
        assert precheck["console_run_id"] == "20260305150000-c0de"

    def test_update_proof_merges(self, tmp_path):
        mod = _load_module()
        out_dir = tmp_path / "run_dir"
        out_dir.mkdir()
        mod.write_initial_proof_files(out_dir, "test_run_456")
        mod._update_proof(out_dir, "test_run_456", {"phase": "precheck", "extra": "val"})

        proof = json.loads((out_dir / "PROOF.json").read_text())
        assert proof["run_id"] == "test_run_456"
        assert proof["status"] == "RUNNING"
        assert proof["phase"] == "precheck"
        assert proof["extra"] == "val"
        assert "started_at" in proof

    def test_update_precheck_merges(self, tmp_path):
        mod = _load_module()
        out_dir = tmp_path / "run_dir"
        out_dir.mkdir()
        mod.write_initial_proof_files(out_dir, "test_run_789")
        mod._update_precheck(out_dir, "test_run_789", {
            "status": "FAIL", "error_class": "NOVNC_NOT_READY",
        })

        precheck = json.loads((out_dir / "PRECHECK.json").read_text())
        assert precheck["run_id"] == "test_run_789"
        assert precheck["status"] == "FAIL"
        assert precheck["error_class"] == "NOVNC_NOT_READY"
        assert precheck["precheck"] == "pending"


class TestLatestRunPointer:
    """LATEST_RUN.json pointer should map console run id to the concrete run directory."""

    def test_write_latest_run_pointer_contains_run_id_and_run_dir(self, tmp_path, monkeypatch):
        artifacts_root = tmp_path / "artifacts_root"
        artifacts_root.mkdir()
        monkeypatch.setenv("OPENCLAW_ARTIFACTS_ROOT", str(artifacts_root))
        mod = _load_module()

        out_dir = (
            tmp_path
            / "artifacts"
            / "soma_kajabi"
            / "run_to_done"
            / "run_to_done_20260304T155603Z_abcdef12"
        )
        out_dir.mkdir(parents=True)

        mod.write_latest_run_pointer(
            out_dir=out_dir,
            run_id="20260304155603-a0c4",
            status="RUNNING",
        )

        pointer_path = artifacts_root / "soma_kajabi" / "run_to_done" / "LATEST_RUN.json"
        assert pointer_path.exists(), f"Pointer should be at canonical path: {pointer_path}"
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))

        assert pointer["run_id"] == "20260304155603-a0c4"
        assert pointer["console_run_id"] is None
        assert pointer["run_dir"] == "run_to_done_20260304T155603Z_abcdef12"
        assert pointer["status"] == "RUNNING"
        assert "updated_at" in pointer

    def test_pointer_written_to_env_artifacts_root(self, tmp_path, monkeypatch):
        """When OPENCLAW_ARTIFACTS_ROOT is set, pointer is written under that dir."""
        custom_root = tmp_path / "custom_artifacts"
        custom_root.mkdir()
        monkeypatch.setenv("OPENCLAW_ARTIFACTS_ROOT", str(custom_root))
        mod = _load_module()

        out_dir = tmp_path / "some" / "other" / "path" / "run_20260304"
        out_dir.mkdir(parents=True)

        mod.write_latest_run_pointer(
            out_dir=out_dir,
            run_id="test-env-pointer",
            status="SUCCESS",
        )

        expected_pointer = custom_root / "soma_kajabi" / "run_to_done" / "LATEST_RUN.json"
        assert expected_pointer.exists(), f"Pointer must exist at {expected_pointer}"
        pointer = json.loads(expected_pointer.read_text(encoding="utf-8"))
        assert pointer["run_id"] == "test-env-pointer"
        assert pointer["console_run_id"] is None
        assert pointer["status"] == "SUCCESS"

    def test_pointer_creates_parent_dirs(self, tmp_path, monkeypatch):
        """Pointer write creates parent dirs if they don't exist."""
        new_root = tmp_path / "brand_new_artifacts"
        monkeypatch.setenv("OPENCLAW_ARTIFACTS_ROOT", str(new_root))
        mod = _load_module()

        out_dir = tmp_path / "run_dir"
        out_dir.mkdir()

        mod.write_latest_run_pointer(
            out_dir=out_dir,
            run_id="test-mkdir",
            status="RUNNING",
        )

        expected_pointer = new_root / "soma_kajabi" / "run_to_done" / "LATEST_RUN.json"
        assert expected_pointer.exists()
        assert expected_pointer.parent.is_dir()

    def test_main_writes_console_run_id_from_env(self, tmp_path, monkeypatch):
        root = tmp_path / "repo"
        root.mkdir()
        (root / "config" / "project_state.json").parent.mkdir(parents=True)
        (root / "config" / "project_state.json").write_text('{"projects":{}}')

        artifacts_root = tmp_path / "canonical_artifacts"
        artifacts_root.mkdir()
        monkeypatch.setenv("OPENCLAW_ARTIFACTS_ROOT", str(artifacts_root))
        monkeypatch.setenv("OPENCLAW_CONSOLE_RUN_ID", "20260305141500-c0de")
        monkeypatch.delenv("OPENCLAW_RUN_ID", raising=False)

        mod = _load_module()
        mod._repo_root = lambda: root
        mod._precheck_drift = lambda *a: True
        mod._precheck_hostd = lambda: False

        with patch("sys.argv", ["soma_run_to_done.py"]):
            rc = mod.main()

        assert rc == 1
        pointer_path = artifacts_root / "soma_kajabi" / "run_to_done" / "LATEST_RUN.json"
        assert pointer_path.exists()
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        assert pointer["console_run_id"] == "20260305141500-c0de"
        assert isinstance(pointer.get("run_id"), str) and pointer["run_id"]
        assert str(pointer.get("run_dir", "")).startswith("run_to_done_")
        proof_dir = next((root / "artifacts" / "soma_kajabi" / "run_to_done").iterdir())
        proof = json.loads((proof_dir / "PROOF.json").read_text())
        precheck = json.loads((proof_dir / "PRECHECK.json").read_text())
        assert proof["console_run_id"] == "20260305141500-c0de"
        assert precheck["console_run_id"] == "20260305141500-c0de"


class TestProofExistsEarlyOnPrecheckFail:
    """PROOF.json and PRECHECK.json must exist even when prechecks fail."""

    def test_hostd_fail_writes_proof_and_precheck(self, tmp_path):
        root = tmp_path / "repo"
        root.mkdir()
        (root / "config" / "project_state.json").parent.mkdir(parents=True)
        (root / "config" / "project_state.json").write_text('{"projects":{}}')

        mod = _load_module()
        mod._repo_root = lambda: root
        mod._precheck_drift = lambda *a: True
        mod._precheck_hostd = lambda: False

        with patch("sys.argv", ["soma_run_to_done.py"]):
            rc = mod.main()

        assert rc == 1
        proof_dirs = [
            p for p in (root / "artifacts" / "soma_kajabi" / "run_to_done").iterdir() if p.is_dir()
        ]
        assert len(proof_dirs) == 1
        proof = json.loads((proof_dirs[0] / "PROOF.json").read_text())
        precheck = json.loads((proof_dirs[0] / "PRECHECK.json").read_text())

        assert proof["status"] == "FAIL"
        assert proof["error_class"] == "HOSTD_UNREACHABLE"
        assert proof["phase"] == "precheck"
        assert "started_at" in proof

        assert precheck["status"] == "FAIL"
        assert precheck["error_class"] == "HOSTD_UNREACHABLE"


def _setup_run_to_done_env(tmp_path, *, mirror_exceptions=0, acceptance_exists=True, mirror_report_exists=True):
    """Set up a mock environment where auto_finish returned SUCCESS with given acceptance state."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "config" / "project_state.json").parent.mkdir(parents=True)
    (root / "config" / "project_state.json").write_text('{"projects":{}}')

    auto_run_id = "20260303221322-test"
    af_dir = root / "artifacts" / "soma_kajabi" / "auto_finish" / auto_run_id
    af_dir.mkdir(parents=True)

    accept_rel = f"artifacts/soma_kajabi/acceptance/{auto_run_id}"
    if acceptance_exists:
        accept_dir = root / accept_rel
        accept_dir.mkdir(parents=True)
        if mirror_report_exists:
            exceptions = [{"module": "M1", "title": f"L{i}", "reason": "missing"} for i in range(mirror_exceptions)]
            mr = {"pass": mirror_exceptions == 0, "exceptions": exceptions}
            (accept_dir / "mirror_report.json").write_text(json.dumps(mr))

    summary = {
        "ok": True,
        "artifact_dirs": {"acceptance": accept_rel} if acceptance_exists else {},
    }
    (af_dir / "SUMMARY.json").write_text(json.dumps(summary))
    (af_dir / "RESULT.json").write_text(json.dumps({"status": "SUCCESS"}))

    return root, auto_run_id


class TestRunToDoneMirrorGate:
    """soma_run_to_done must FAIL when mirror_pass is false."""

    def test_mirror_pass_true_returns_success(self, tmp_path):
        root, auto_run_id = _setup_run_to_done_env(tmp_path, mirror_exceptions=0)
        mod = _load_module()
        mod._repo_root = lambda: root
        mod._precheck_drift = lambda *a: True
        mod._precheck_hostd = lambda: True
        mod._precheck_novnc = lambda *a, **kw: True

        mock_tr = MagicMock()
        mock_tr.state = "ACCEPTED"
        mock_tr.run_id = auto_run_id
        mock_tr.status_code = 200

        with patch("sys.argv", ["soma_run_to_done.py"]), \
             patch.object(mod, "trigger_exec", return_value=mock_tr), \
             patch.object(mod, "hq_request", return_value=(200, json.dumps({
                 "run": {"status": "completed", "artifact_dir": f"artifacts/soma_kajabi/auto_finish/{auto_run_id}"}
             }))):
            rc = mod.main()

        assert rc == 0
        proof_dirs = [
            p for p in (root / "artifacts" / "soma_kajabi" / "run_to_done").iterdir() if p.is_dir()
        ]
        assert len(proof_dirs) == 1
        proof = json.loads((proof_dirs[0] / "PROOF.json").read_text())
        assert proof["status"] == "SUCCESS"
        assert proof["mirror_pass"] is True

    def test_mirror_fail_returns_failure(self, tmp_path):
        root, auto_run_id = _setup_run_to_done_env(tmp_path, mirror_exceptions=2)
        mod = _load_module()
        mod._repo_root = lambda: root
        mod._precheck_drift = lambda *a: True
        mod._precheck_hostd = lambda: True
        mod._precheck_novnc = lambda *a, **kw: True

        mock_tr = MagicMock()
        mock_tr.state = "ACCEPTED"
        mock_tr.run_id = auto_run_id

        with patch("sys.argv", ["soma_run_to_done.py"]), \
             patch.object(mod, "trigger_exec", return_value=mock_tr), \
             patch.object(mod, "hq_request", return_value=(200, json.dumps({
                 "run": {"status": "completed", "artifact_dir": f"artifacts/soma_kajabi/auto_finish/{auto_run_id}"}
             }))):
            rc = mod.main()

        assert rc == 1
        proof_dirs = [
            p for p in (root / "artifacts" / "soma_kajabi" / "run_to_done").iterdir() if p.is_dir()
        ]
        proof = json.loads((proof_dirs[0] / "PROOF.json").read_text())
        assert proof["status"] == "FAILURE"
        assert proof["error_class"] == "MIRROR_FAIL"
        assert proof["mirror_pass"] is False
        assert proof["mirror_exceptions_count"] == 2

    def test_acceptance_missing_returns_failure(self, tmp_path):
        root, auto_run_id = _setup_run_to_done_env(tmp_path, acceptance_exists=False)
        mod = _load_module()
        mod._repo_root = lambda: root
        mod._precheck_drift = lambda *a: True
        mod._precheck_hostd = lambda: True
        mod._precheck_novnc = lambda *a, **kw: True

        mock_tr = MagicMock()
        mock_tr.state = "ACCEPTED"
        mock_tr.run_id = auto_run_id

        with patch("sys.argv", ["soma_run_to_done.py"]), \
             patch.object(mod, "trigger_exec", return_value=mock_tr), \
             patch.object(mod, "hq_request", return_value=(200, json.dumps({
                 "run": {"status": "completed", "artifact_dir": f"artifacts/soma_kajabi/auto_finish/{auto_run_id}"}
             }))):
            rc = mod.main()

        assert rc == 1
        proof_dirs = [
            p for p in (root / "artifacts" / "soma_kajabi" / "run_to_done").iterdir() if p.is_dir()
        ]
        proof = json.loads((proof_dirs[0] / "PROOF.json").read_text())
        assert proof["status"] == "FAILURE"
        assert proof["error_class"] == "ACCEPTANCE_MISSING_FOR_RUN"

    def test_mirror_report_missing_returns_failure(self, tmp_path):
        root, auto_run_id = _setup_run_to_done_env(
            tmp_path, acceptance_exists=True, mirror_report_exists=False
        )
        mod = _load_module()
        mod._repo_root = lambda: root
        mod._precheck_drift = lambda *a: True
        mod._precheck_hostd = lambda: True
        mod._precheck_novnc = lambda *a, **kw: True

        mock_tr = MagicMock()
        mock_tr.state = "ACCEPTED"
        mock_tr.run_id = auto_run_id

        with patch("sys.argv", ["soma_run_to_done.py"]), \
             patch.object(mod, "trigger_exec", return_value=mock_tr), \
             patch.object(mod, "hq_request", return_value=(200, json.dumps({
                 "run": {"status": "completed", "artifact_dir": f"artifacts/soma_kajabi/auto_finish/{auto_run_id}"}
             }))):
            rc = mod.main()

        assert rc == 1
        proof_dirs = [
            p for p in (root / "artifacts" / "soma_kajabi" / "run_to_done").iterdir() if p.is_dir()
        ]
        proof = json.loads((proof_dirs[0] / "PROOF.json").read_text())
        assert proof["status"] == "FAILURE"
        assert proof["error_class"] == "ACCEPTANCE_MISSING_FOR_RUN"
