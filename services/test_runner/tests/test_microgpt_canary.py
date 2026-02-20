"""Hermetic tests for llm.microgpt.canary: SHA256 validation, artifact layout, no network."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys

import pytest

# Repo root (parent of services/test_runner)
REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")
)
CANARY_SCRIPT = os.path.join(REPO_ROOT, "ops", "scripts", "microgpt_canary.sh")
FIXTURE_STUB = os.path.join(
    os.path.dirname(__file__), "fixtures", "microgpt_stub.py"
)


def _sha256(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


@pytest.fixture
def artifact_dir(tmp_path):
    """ARTIFACT_DIR for canary; parent is ARTIFACTS_ROOT for cache."""
    job_id = "test-microgpt-001"
    art_root = tmp_path / "artifacts"
    art_dir = art_root / job_id
    art_dir.mkdir(parents=True)
    return str(art_dir)


@pytest.fixture
def stub_sha256():
    """SHA256 of the fixture stub (used for MICROGPT_EXPECTED_SHA256)."""
    assert os.path.isfile(FIXTURE_STUB), f"Fixture missing: {FIXTURE_STUB}"
    return _sha256(FIXTURE_STUB)


def test_microgpt_canary_resolve_job():
    """llm.microgpt.canary is in job allowlist and has expected argv/timeout."""
    from test_runner.allowlist import resolve_job

    allowlist_path = os.path.join(REPO_ROOT, "configs", "job_allowlist.yaml")
    if not os.path.isfile(allowlist_path):
        pytest.skip("configs/job_allowlist.yaml not found (run from repo root)")
    job = resolve_job("llm.microgpt.canary", path=allowlist_path)
    assert job.name == "llm.microgpt.canary"
    assert "microgpt_canary.sh" in str(job.argv)
    assert job.timeout_sec == 90


def test_microgpt_canary_sha256_mismatch_fails(artifact_dir, tmp_path):
    """When fetched/cached content has wrong SHA256, script exits non-zero."""
    if not os.path.isfile(CANARY_SCRIPT):
        pytest.skip("Canary script not found (not in repo root)")
    bad_file = tmp_path / "wrong.py"
    bad_file.write_text("wrong content")
    # Use a different expected SHA so that when script fetches bad_file it fails verify
    env = os.environ.copy()
    env["ARTIFACT_DIR"] = artifact_dir
    env["MICROGPT_EXPECTED_SHA256"] = "0" * 64
    env["MICROGPT_RAW_URL"] = f"file://{bad_file}"
    proc = subprocess.run(
        ["/usr/bin/env", "bash", CANARY_SCRIPT],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert proc.returncode != 0
    assert "mismatch" in (proc.stderr or "") or "mismatch" in (proc.stdout or "")


def test_microgpt_canary_artifacts_produced(artifact_dir, stub_sha256):
    """With valid cached stub, canary runs and writes summary.json + stdout.log + samples."""
    if not os.path.isfile(CANARY_SCRIPT):
        pytest.skip("Canary script not found (not in repo root)")
    if not os.path.isfile(FIXTURE_STUB):
        pytest.skip("Fixture stub not found")

    art_root = os.path.dirname(artifact_dir)
    cache_dir = os.path.join(art_root, "cache", "microgpt_canary")
    os.makedirs(cache_dir, exist_ok=True)
    # Cache the stub; script uses EXPECTED_SHA256 = stub hash so verification passes
    import shutil
    shutil.copy(FIXTURE_STUB, os.path.join(cache_dir, "microgpt.py"))

    env = os.environ.copy()
    env["ARTIFACT_DIR"] = artifact_dir
    env["MICROGPT_EXPECTED_SHA256"] = stub_sha256

    proc = subprocess.run(
        ["/usr/bin/env", "bash", CANARY_SCRIPT],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    out_dir = os.path.join(artifact_dir, "microgpt_canary")
    summary_path = os.path.join(out_dir, "summary.json")
    stdout_path = os.path.join(out_dir, "stdout.log")

    assert os.path.isfile(summary_path), f"summary.json not written: {out_dir!r}"
    assert os.path.isfile(stdout_path), f"stdout.log not written: {out_dir!r}"

    with open(summary_path) as f:
        summary = json.load(f)
    assert "ok" in summary
    assert "steps" in summary
    assert "runtime_ms" in summary
    assert "sha256" in summary
    assert summary["sha256"] == stub_sha256
    assert summary.get("samples_preview") is not None

    # Script should exit 0 when ok=1
    if summary.get("ok") is True:
        assert proc.returncode == 0
    else:
        # Parser may not find loss line in stub output; ok can be false
        assert proc.returncode in (0, 1)
