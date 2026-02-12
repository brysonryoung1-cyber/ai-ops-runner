"""Integration tests for ORB job types — allowlist, params, mutation detection, artifacts."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile

import pytest
import yaml

os.environ.setdefault("ALLOWLIST_PATH", "/dev/null")
os.environ.setdefault("REPO_ALLOWLIST_PATH", "/dev/null")

from test_runner.allowlist import AllowedJob, load_allowlist, resolve_job
from test_runner.security import assert_worktree_clean, make_readonly


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def orb_allowlist(tmp_path):
    """Job allowlist with all ORB job types."""
    data = {
        "jobs": {
            "orb_review_bundle": {
                "argv": ["bash", "/app/orb_wrappers/orb_review_bundle.sh"],
                "timeout_sec": 1800,
                "allowed_params": ["since_sha"],
                "requires_repo_allowlist": True,
            },
            "orb_doctor": {
                "argv": ["bash", "/app/orb_wrappers/orb_doctor.sh"],
                "timeout_sec": 600,
                "requires_repo_allowlist": True,
            },
            "orb_score_run": {
                "argv": ["bash", "/app/orb_wrappers/orb_score_run.sh"],
                "timeout_sec": 1800,
                "allowed_params": ["logs_day", "run_id"],
                "requires_repo_allowlist": True,
            },
        }
    }
    f = tmp_path / "allowlist.yaml"
    f.write_text(yaml.dump(data))
    return str(f)


@pytest.fixture
def git_repo(tmp_path):
    """Create a simple git repo to simulate ORB worktree."""
    repo = str(tmp_path / "orb_repo")
    os.makedirs(repo)
    subprocess.check_call(["git", "init", repo])
    subprocess.check_call(
        ["git", "-C", repo, "config", "user.email", "test@test.com"]
    )
    subprocess.check_call(
        ["git", "-C", repo, "config", "user.name", "Test"]
    )
    test_file = os.path.join(repo, "README.md")
    with open(test_file, "w") as f:
        f.write("# Test ORB repo\n")
    subprocess.check_call(["git", "-C", repo, "add", "."])
    subprocess.check_call(["git", "-C", repo, "commit", "-m", "init"])
    return repo


# ---------------------------------------------------------------------------
# Job allowlist tests
# ---------------------------------------------------------------------------

def test_orb_review_bundle_in_allowlist(orb_allowlist):
    job = resolve_job("orb_review_bundle", orb_allowlist)
    assert job.name == "orb_review_bundle"
    assert job.timeout_sec == 1800
    assert "since_sha" in job.allowed_params
    assert job.requires_repo_allowlist is True


def test_orb_doctor_in_allowlist(orb_allowlist):
    job = resolve_job("orb_doctor", orb_allowlist)
    assert job.name == "orb_doctor"
    assert job.timeout_sec == 600
    assert len(job.allowed_params) == 0
    assert job.requires_repo_allowlist is True


def test_orb_score_run_in_allowlist(orb_allowlist):
    job = resolve_job("orb_score_run", orb_allowlist)
    assert job.name == "orb_score_run"
    assert "logs_day" in job.allowed_params
    assert "run_id" in job.allowed_params
    assert job.requires_repo_allowlist is True


def test_orb_job_rejects_unknown_type(orb_allowlist):
    with pytest.raises(ValueError, match="not in allowlist"):
        resolve_job("evil_job_type", orb_allowlist)


# ---------------------------------------------------------------------------
# Param validation tests
# ---------------------------------------------------------------------------

def test_orb_review_bundle_valid_param(orb_allowlist):
    job = resolve_job("orb_review_bundle", orb_allowlist)
    assert "since_sha" in job.allowed_params


def test_orb_review_bundle_rejects_bad_param(orb_allowlist):
    job = resolve_job("orb_review_bundle", orb_allowlist)
    assert "evil_param" not in job.allowed_params


def test_orb_score_run_valid_params(orb_allowlist):
    job = resolve_job("orb_score_run", orb_allowlist)
    assert "logs_day" in job.allowed_params
    assert "run_id" in job.allowed_params
    assert "shell_cmd" not in job.allowed_params


# ---------------------------------------------------------------------------
# Mutation detection tests
# ---------------------------------------------------------------------------

def test_clean_worktree_passes(git_repo):
    """Clean worktree should pass assertion."""
    assert_worktree_clean(git_repo)


def test_mutation_detected_on_dirty_worktree(git_repo):
    """MUTATION_DETECTED: modifying a tracked file triggers assertion."""
    readme = os.path.join(git_repo, "README.md")
    with open(readme, "w") as f:
        f.write("# MUTATED\n")
    with pytest.raises(RuntimeError, match="Worktree dirty"):
        assert_worktree_clean(git_repo)


def test_mutation_detected_on_new_file(git_repo):
    """MUTATION_DETECTED: creating an untracked file triggers assertion."""
    new_file = os.path.join(git_repo, "injected.txt")
    with open(new_file, "w") as f:
        f.write("evil content")
    with pytest.raises(RuntimeError, match="Worktree dirty"):
        assert_worktree_clean(git_repo)


def test_readonly_worktree_prevents_writes(git_repo):
    """Read-only worktree should prevent file modification."""
    make_readonly(git_repo)
    readme = os.path.join(git_repo, "README.md")
    with pytest.raises(PermissionError):
        with open(readme, "w") as f:
            f.write("should fail")


def test_readonly_preserves_clean_state(git_repo):
    """Read-only worktree should still pass clean-tree assertion."""
    make_readonly(git_repo)
    assert_worktree_clean(git_repo)


# ---------------------------------------------------------------------------
# Artifact contract tests
# ---------------------------------------------------------------------------

def test_artifact_json_includes_invariants(tmp_path):
    """Verify artifact.json schema includes invariants field."""
    from test_runner.artifacts import write_artifact_json, read_artifact_json

    import test_runner.artifacts as arts
    monkeypatch_root = str(tmp_path)
    original_root = arts.ARTIFACTS_ROOT
    arts.ARTIFACTS_ROOT = monkeypatch_root

    try:
        data = {
            "job_id": "test-orb-123",
            "repo_name": "algo-nt8-orb",
            "remote_url": "git@github.com:brysonryoung1-cyber/algo-nt8-orb.git",
            "sha": "abc123",
            "job_type": "orb_review_bundle",
            "argv": ["bash", "/app/orb_wrappers/orb_review_bundle.sh"],
            "timeout_sec": 1800,
            "started_at": "2026-01-01T00:00:00Z",
            "finished_at": "2026-01-01T00:00:10Z",
            "duration_ms": 10000,
            "exit_code": 0,
            "status": "success",
            "hostname": "runner-1",
            "trace_id": "trace-abc",
            "input_hash": "hash-in",
            "allowlist_hash": "hash-al",
            "stdout_path": "/artifacts/test-orb-123/stdout.log",
            "stderr_path": "/artifacts/test-orb-123/stderr.log",
            "params": {"since_sha": "def456"},
            "outputs": ["REVIEW_BUNDLE.txt", "stdout.log", "stderr.log", "artifact.json"],
            "invariants": {
                "read_only_ok": True,
                "clean_tree_ok": True,
            },
        }
        write_artifact_json("test-orb-123", data)
        loaded = read_artifact_json("test-orb-123")
        assert loaded["invariants"]["read_only_ok"] is True
        assert loaded["invariants"]["clean_tree_ok"] is True
        assert loaded["params"] == {"since_sha": "def456"}
        assert "REVIEW_BUNDLE.txt" in loaded["outputs"]
    finally:
        arts.ARTIFACTS_ROOT = original_root
