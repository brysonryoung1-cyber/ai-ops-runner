"""Tests for acceptance_artifacts module."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_write_acceptance_artifacts_mirror_pass(tmp_path):
    """Mirror pass: Home above-paywall lessons exist in Practitioner."""
    root = tmp_path / "repo"
    root.mkdir()
    phase0_dir = root / "artifacts" / "soma_kajabi" / "phase0" / "phase0_20250101T120000Z_abc"
    phase0_dir.mkdir(parents=True)
    snap = {
        "home": {
            "modules": ["M1"],
            "lessons": [
                {"module_name": "M1", "title": "L1", "above_paywall": "yes"},
                {"module_name": "M1", "title": "L2", "above_paywall": "no"},
            ],
        },
        "practitioner": {
            "modules": ["M1"],
            "lessons": [
                {"module_name": "M1", "title": "L1"},
                {"module_name": "M1", "title": "L2"},
            ],
        },
    }
    (phase0_dir / "kajabi_library_snapshot.json").write_text(json.dumps(snap))
    (phase0_dir / "video_manifest.csv").write_text(
        "email_id,subject,file_name,sha256,rough_topic,proposed_module,proposed_lesson_title,proposed_description,status\n"
    )

    from services.soma_kajabi.acceptance_artifacts import write_acceptance_artifacts

    run_id = "auto_finish_20250101T120000Z_xyz"
    accept_dir, summary = write_acceptance_artifacts(root, run_id, phase0_dir)

    assert summary["pass"] is True
    assert summary["exceptions_count"] == 0
    assert (accept_dir / "final_library_snapshot.json").exists()
    assert (accept_dir / "video_manifest.csv").exists()
    assert (accept_dir / "mirror_report.json").exists()
    assert (accept_dir / "changelog.md").exists()
    mirror = json.loads((accept_dir / "mirror_report.json").read_text())
    assert mirror["pass"] is True
    assert mirror["exceptions"] == []


def test_write_acceptance_artifacts_mirror_fail(tmp_path):
    """Mirror fail: Home above-paywall lesson missing in Practitioner."""
    root = tmp_path / "repo"
    root.mkdir()
    phase0_dir = root / "artifacts" / "soma_kajabi" / "phase0" / "phase0_20250101T120000Z_abc"
    phase0_dir.mkdir(parents=True)
    snap = {
        "home": {
            "modules": ["M1"],
            "lessons": [{"module_name": "M1", "title": "L1", "above_paywall": "yes"}],
        },
        "practitioner": {"modules": [], "lessons": []},
    }
    (phase0_dir / "kajabi_library_snapshot.json").write_text(json.dumps(snap))
    (phase0_dir / "video_manifest.csv").write_text(
        "email_id,subject,file_name,sha256,rough_topic,proposed_module,proposed_lesson_title,proposed_description,status\n"
    )

    from services.soma_kajabi.acceptance_artifacts import write_acceptance_artifacts

    run_id = "auto_finish_20250101T120000Z_xyz"
    accept_dir, summary = write_acceptance_artifacts(root, run_id, phase0_dir)

    assert summary["pass"] is False
    assert summary["exceptions_count"] == 1
    mirror = json.loads((accept_dir / "mirror_report.json").read_text())
    assert mirror["pass"] is False
    assert len(mirror["exceptions"]) == 1
    assert mirror["exceptions"][0]["reason"] == "missing_in_practitioner"
