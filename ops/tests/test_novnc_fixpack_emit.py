"""Hermetic tests for novnc_fixpack_emit.sh and csr_prompt_from_bundle.sh."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXPACK_SCRIPT = REPO_ROOT / "ops" / "scripts" / "novnc_fixpack_emit.sh"
PROMPT_SCRIPT = REPO_ROOT / "ops" / "scripts" / "csr_prompt_from_bundle.sh"
EVIDENCE_SCRIPT = REPO_ROOT / "ops" / "scripts" / "csr_evidence_bundle.sh"


def _run_fixpack(tmp_path, extra_args=None):
    """Run novnc_fixpack_emit.sh in a temp directory."""
    triage_dir = tmp_path / "triage"
    triage_dir.mkdir()

    # Create a fake artifact for pointer
    fake_log = tmp_path / "fake.log"
    fake_log.write_text("line1\nline2\nline3\n")

    cmd = [
        "bash",
        str(FIXPACK_SCRIPT),
        str(triage_dir),
        "NOVNC_NOT_READY",
        "novnc_autorecover",
        "run novnc_autorecover or escalate",
        f"fake_log:{fake_log}",
    ]
    if extra_args:
        cmd.extend(extra_args)

    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30, cwd=str(REPO_ROOT))
    return r, triage_dir


def test_fixpack_creates_triage_json(tmp_path):
    """triage.json is created with correct schema."""
    r, triage_dir = _run_fixpack(tmp_path)
    assert r.returncode == 0
    triage_path = triage_dir / "triage.json"
    assert triage_path.exists()
    data = json.loads(triage_path.read_text())
    assert data["error_class"] == "NOVNC_NOT_READY"
    assert data["failing_step"] == "novnc_autorecover"
    assert data["recommended_next_action"] == "run novnc_autorecover or escalate"
    assert data["retryable"] is True
    assert "timestamp" in data
    assert "artifact_pointers" in data


def test_fixpack_creates_evidence_bundle(tmp_path):
    """evidence_bundle.json is created."""
    r, triage_dir = _run_fixpack(tmp_path)
    assert r.returncode == 0
    bundle_path = triage_dir / "evidence_bundle.json"
    assert bundle_path.exists()
    data = json.loads(bundle_path.read_text())
    assert data["error_class"] == "NOVNC_NOT_READY"


def test_fixpack_creates_csr_prompt(tmp_path):
    """CSR_PROMPT.txt is created with required fields."""
    r, triage_dir = _run_fixpack(tmp_path)
    assert r.returncode == 0
    prompt_path = triage_dir / "CSR_PROMPT.txt"
    assert prompt_path.exists()
    content = prompt_path.read_text()
    assert "MODE: IMPLEMENTER (Opus)" in content
    assert "error_class: NOVNC_NOT_READY" in content
    assert "ARTIFACT POINTERS:" in content


def test_fixpack_creates_error_summary(tmp_path):
    """ERROR_SUMMARY.txt is created with fixpack path."""
    r, triage_dir = _run_fixpack(tmp_path)
    assert r.returncode == 0
    summary_path = triage_dir / "ERROR_SUMMARY.txt"
    assert summary_path.exists()
    content = summary_path.read_text()
    assert "fixpack_path:" in content
    assert "triage_json:" in content


def test_fixpack_non_retryable_error_class(tmp_path):
    """NOVNC_DOCTOR_MISSING should be non-retryable."""
    triage_dir = tmp_path / "triage"
    triage_dir.mkdir()
    r = subprocess.run(
        [
            "bash",
            str(FIXPACK_SCRIPT),
            str(triage_dir),
            "NOVNC_DOCTOR_MISSING",
            "doctor_check",
            "install doctor script",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(REPO_ROOT),
    )
    assert r.returncode == 0
    data = json.loads((triage_dir / "triage.json").read_text())
    assert data["retryable"] is False


def test_fixpack_usage_error():
    """Too few args → exit 1."""
    r = subprocess.run(
        ["bash", str(FIXPACK_SCRIPT), "/tmp"],
        capture_output=True,
        text=True,
        timeout=10,
        cwd=str(REPO_ROOT),
    )
    assert r.returncode == 1


def test_csr_prompt_standalone(tmp_path):
    """csr_prompt_from_bundle.sh creates CSR_PROMPT.txt from triage.json."""
    triage_dir = tmp_path / "triage"
    triage_dir.mkdir()
    triage = {
        "error_class": "NOVNC_WS_TAILNET_FAILED",
        "failing_step": "ws_probe",
        "recommended_next_action": "check tailscale",
        "artifact_pointers": {"log": "/tmp/test.log"},
    }
    (triage_dir / "triage.json").write_text(json.dumps(triage))
    r = subprocess.run(
        ["bash", str(PROMPT_SCRIPT), str(triage_dir)],
        capture_output=True,
        text=True,
        timeout=10,
        cwd=str(REPO_ROOT),
    )
    assert r.returncode == 0
    prompt = (triage_dir / "CSR_PROMPT.txt").read_text()
    assert "NOVNC_WS_TAILNET_FAILED" in prompt
    assert "log: /tmp/test.log" in prompt
