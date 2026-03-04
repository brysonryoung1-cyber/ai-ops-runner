from __future__ import annotations

import json
from pathlib import Path

from ops.system.brain_loop import main


def test_brain_loop_mock_writes_proof_bundle(tmp_path: Path) -> None:
    artifacts_root = tmp_path / "artifacts"
    state_root = tmp_path / "state" / "brain_loop"

    rc = main(
        [
            "--mode",
            "all",
            "--mock",
            "--mock-status",
            "PASS",
            "--state-root",
            str(state_root),
            "--artifacts-root",
            str(artifacts_root),
        ]
    )
    assert rc == 0

    bundles = sorted((artifacts_root / "system" / "brain_loop").glob("brain_loop_*"))
    assert bundles, "brain loop bundle should exist"
    bundle_dir = bundles[-1]

    result_path = bundle_dir / "RESULT.json"
    summary_path = bundle_dir / "SUMMARY.md"
    ref_path = bundle_dir / "doctor_matrix_ref.json"

    assert result_path.is_file()
    assert summary_path.is_file()
    assert ref_path.is_file()

    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] == "PASS"
    assert result["matrix_status"] == "PASS"

