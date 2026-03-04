"""Hermetic smoke test for doctor matrix mock mode."""

from __future__ import annotations

import json
from pathlib import Path

from ops.system.doctor_matrix import run_doctor_matrix


def test_doctor_matrix_mock_mode_emits_bundle(tmp_path, monkeypatch) -> None:
    artifacts_root = tmp_path / "artifacts"
    monkeypatch.setenv("OPENCLAW_ARTIFACTS_ROOT", str(artifacts_root))

    # Core run-dir contract check reads this pointer from canonical artifacts root.
    run_to_done_root = artifacts_root / "soma_kajabi" / "run_to_done"
    run_to_done_root.mkdir(parents=True, exist_ok=True)
    pointer_payload = {
        "run_id": "20260304120000-abcd",
        "run_dir": "run_to_done_20260304T120000Z_mock1234",
        "status": "SUCCESS",
    }
    (run_to_done_root / "LATEST_RUN.json").write_text(json.dumps(pointer_payload, indent=2) + "\n")
    (run_to_done_root / pointer_payload["run_dir"]).mkdir(parents=True, exist_ok=True)

    exit_code, payload = run_doctor_matrix(
        [
            "--mode",
            "all",
            "--mock",
            "--run-id",
            "doctor_matrix_mock_smoke",
        ]
    )

    assert exit_code == 0
    assert payload["status"] == "PASS"

    bundle_dir = artifacts_root / "system" / "doctor_matrix" / "doctor_matrix_mock_smoke"
    assert bundle_dir.is_dir()

    result_path = bundle_dir / "RESULT.json"
    summary_path = bundle_dir / "SUMMARY.md"
    checks_path = bundle_dir / "checks.json"
    env_path = bundle_dir / "ENV.json"
    version_path = bundle_dir / "VERSION.json"

    assert result_path.is_file()
    assert summary_path.is_file()
    assert checks_path.is_file()
    assert env_path.is_file()
    assert version_path.is_file()

    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] == "PASS"
    assert "core_summary" in result

    summary = summary_path.read_text(encoding="utf-8")
    assert "| Checklist | Scope | Check ID | Status | Message |" in summary

    core_evidence = bundle_dir / "evidence" / "core"
    project_evidence = bundle_dir / "evidence" / "projects" / "soma_kajabi"
    assert core_evidence.is_dir()
    assert project_evidence.is_dir()
