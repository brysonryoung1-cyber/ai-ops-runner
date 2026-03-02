"""Tests for acceptance_artifacts module."""

from __future__ import annotations

import csv
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


def test_video_manifest_spec_columns(tmp_path):
    """Acceptance video_manifest.csv uses SOMA_LOCKED_SPEC §6 columns."""
    root = tmp_path / "repo"
    root.mkdir()
    phase0_dir = root / "artifacts" / "soma_kajabi" / "phase0" / "phase0_test"
    phase0_dir.mkdir(parents=True)
    snap = {
        "home": {"modules": ["M1"], "lessons": []},
        "practitioner": {"modules": ["M1"], "lessons": []},
    }
    (phase0_dir / "kajabi_library_snapshot.json").write_text(json.dumps(snap))
    (phase0_dir / "video_manifest.csv").write_text(
        "email_id,subject,file_name,sha256,rough_topic,proposed_module,proposed_lesson_title,proposed_description,status\n"
        "abc123,Breathwork Session 1,breathwork1.mp4,deadbeef,,Module 1,Intro to Breathwork,,unmapped\n"
        "def456,Deep Dive,deepdive.mp4,cafebabe,,Module 2,Deep Dive Overview,,mapped\n"
    )

    from services.soma_kajabi.acceptance_artifacts import write_acceptance_artifacts

    accept_dir, _ = write_acceptance_artifacts(root, "test_run", phase0_dir)
    manifest_path = accept_dir / "video_manifest.csv"
    assert manifest_path.exists()

    with manifest_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)

    assert fieldnames == ["subject", "timestamp", "filename", "mapped_lesson", "status"]
    assert len(rows) == 2
    assert rows[0]["subject"] == "Breathwork Session 1"
    assert rows[0]["filename"] == "breathwork1.mp4"
    assert rows[0]["mapped_lesson"] == "Intro to Breathwork"
    assert rows[0]["status"] == "raw_needs_review"
    assert rows[1]["status"] == "attached"


def test_video_manifest_status_normalization(tmp_path):
    """Status values are normalized: mapped→attached, unmapped→raw_needs_review."""
    from services.soma_kajabi.acceptance_artifacts import _normalize_manifest_status

    assert _normalize_manifest_status("attached") == "attached"
    assert _normalize_manifest_status("mapped") == "attached"
    assert _normalize_manifest_status("unmapped") == "raw_needs_review"
    assert _normalize_manifest_status("raw_needs_review") == "raw_needs_review"
    assert _normalize_manifest_status("") == "raw_needs_review"
    assert _normalize_manifest_status("ATTACHED") == "attached"


def test_mirror_report_schema(tmp_path):
    """Mirror report has required schema fields per SOMA_ACCEPTANCE_CHECKLIST."""
    root = tmp_path / "repo"
    root.mkdir()
    phase0_dir = root / "artifacts" / "soma_kajabi" / "phase0" / "phase0_test"
    phase0_dir.mkdir(parents=True)
    snap = {
        "home": {
            "modules": ["M1"],
            "lessons": [
                {"module_name": "M1", "title": "L1", "above_paywall": "yes", "attached_video_name": "v1.mp4"},
            ],
        },
        "practitioner": {
            "modules": ["M1"],
            "lessons": [
                {"module_name": "M1", "title": "L1", "attached_video_name": "v1.mp4"},
            ],
        },
    }
    (phase0_dir / "kajabi_library_snapshot.json").write_text(json.dumps(snap))
    (phase0_dir / "video_manifest.csv").write_text(
        "email_id,subject,file_name,sha256,rough_topic,proposed_module,proposed_lesson_title,proposed_description,status\n"
    )

    from services.soma_kajabi.acceptance_artifacts import write_acceptance_artifacts

    accept_dir, summary = write_acceptance_artifacts(root, "test_run", phase0_dir)
    mirror = json.loads((accept_dir / "mirror_report.json").read_text())

    assert "schema_version" in mirror
    assert "generated_at" in mirror
    assert mirror["source"] == "Home User Library"
    assert mirror["target"] == "Practitioner Library"
    assert "pass" in mirror
    assert "exceptions" in mirror
    assert "exceptions_count" in mirror
    assert isinstance(mirror["exceptions"], list)
    assert mirror["pass"] is True
    assert mirror["exceptions_count"] == 0


def test_mirror_video_mismatch_detected(tmp_path):
    """Video mismatch between Home and Practitioner is caught as exception."""
    root = tmp_path / "repo"
    root.mkdir()
    phase0_dir = root / "artifacts" / "soma_kajabi" / "phase0" / "phase0_test"
    phase0_dir.mkdir(parents=True)
    snap = {
        "home": {
            "modules": ["M1"],
            "lessons": [
                {"module_name": "M1", "title": "L1", "above_paywall": "yes", "attached_video_name": "v1.mp4"},
            ],
        },
        "practitioner": {
            "modules": ["M1"],
            "lessons": [
                {"module_name": "M1", "title": "L1", "attached_video_name": "v2.mp4"},
            ],
        },
    }
    (phase0_dir / "kajabi_library_snapshot.json").write_text(json.dumps(snap))
    (phase0_dir / "video_manifest.csv").write_text(
        "email_id,subject,file_name,sha256,rough_topic,proposed_module,proposed_lesson_title,proposed_description,status\n"
    )

    from services.soma_kajabi.acceptance_artifacts import write_acceptance_artifacts

    _, summary = write_acceptance_artifacts(root, "test_run", phase0_dir)
    assert summary["pass"] is False
    assert summary["exceptions_count"] == 1
