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
        proof_dirs = list((root / "artifacts" / "soma_kajabi" / "run_to_done").iterdir())
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
        proof_dirs = list((root / "artifacts" / "soma_kajabi" / "run_to_done").iterdir())
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
        proof_dirs = list((root / "artifacts" / "soma_kajabi" / "run_to_done").iterdir())
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
        proof_dirs = list((root / "artifacts" / "soma_kajabi" / "run_to_done").iterdir())
        proof = json.loads((proof_dirs[0] / "PROOF.json").read_text())
        assert proof["status"] == "FAILURE"
        assert proof["error_class"] == "ACCEPTANCE_MISSING_FOR_RUN"
