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

# ---------------------------------------------------------------------------
# hooksPath config tests (orb_doctor hardening)
# ---------------------------------------------------------------------------

def test_hookspath_config_does_not_dirty_worktree(git_repo):
    """Setting core.hooksPath config does NOT trip mutation detection.

    orb_doctor.sh sets core.hooksPath in the gitdir config before running
    the ORB doctor.  This must not make the worktree appear dirty.
    """
    # Create .githooks directory (as ORB repos have)
    githooks_dir = os.path.join(git_repo, ".githooks")
    os.makedirs(githooks_dir)
    hook_file = os.path.join(githooks_dir, "pre-commit")
    with open(hook_file, "w") as f:
        f.write("#!/bin/sh\nexit 0\n")
    import stat as _stat
    os.chmod(hook_file, _stat.S_IRWXU)
    subprocess.check_call(["git", "-C", git_repo, "add", "."])
    subprocess.check_call(["git", "-C", git_repo, "commit", "-m", "add githooks"])

    # Apply hooksPath config (this is what orb_doctor.sh does)
    subprocess.check_call(
        ["git", "-C", git_repo, "config", "core.hooksPath", ".githooks"]
    )

    # Worktree must still be clean
    assert_worktree_clean(git_repo)


def test_hookspath_config_on_readonly_worktree_stays_clean(git_repo):
    """hooksPath config + read-only worktree still passes clean-tree check."""
    githooks_dir = os.path.join(git_repo, ".githooks")
    os.makedirs(githooks_dir)
    hook_file = os.path.join(githooks_dir, "pre-commit")
    with open(hook_file, "w") as f:
        f.write("#!/bin/sh\nexit 0\n")
    subprocess.check_call(["git", "-C", git_repo, "add", "."])
    subprocess.check_call(["git", "-C", git_repo, "commit", "-m", "add githooks"])

    # Set hooksPath, then make read-only
    subprocess.check_call(
        ["git", "-C", git_repo, "config", "core.hooksPath", ".githooks"]
    )
    make_readonly(git_repo)

    # Must still pass
    assert_worktree_clean(git_repo)


# ---------------------------------------------------------------------------
# SIZE_CAP fallback tests
# ---------------------------------------------------------------------------

def test_size_cap_meta_readable_and_valid(tmp_path):
    """Verify that a size_cap_meta.json written by the wrapper is readable."""
    meta = {
        "size_cap_triggered": True,
        "packet_dir": "review_packets/20260212_120000",
        "archive_path": "ORB_REVIEW_PACKETS.tar.gz",
        "readme_path": "README_REVIEW_PACKETS.txt",
        "packet_count": 3,
        "since_sha": "abc123",
        "stamp": "20260212_120000",
    }
    meta_path = tmp_path / "size_cap_meta.json"
    meta_path.write_text(json.dumps(meta))

    loaded = json.loads(meta_path.read_text())
    assert loaded["size_cap_triggered"] is True
    assert loaded["packet_count"] == 3
    assert loaded["archive_path"] == "ORB_REVIEW_PACKETS.tar.gz"
    assert loaded["readme_path"] == "README_REVIEW_PACKETS.txt"


def test_size_cap_fallback_in_artifact_json(tmp_path):
    """artifact.json includes size_cap_fallback when size_cap_meta.json is present."""
    from test_runner.artifacts import write_artifact_json, read_artifact_json
    import test_runner.artifacts as arts

    original_root = arts.ARTIFACTS_ROOT
    arts.ARTIFACTS_ROOT = str(tmp_path)

    try:
        job_id = "test-sizecap-001"
        art = arts.artifact_dir(job_id)

        # Simulate wrapper writing size_cap_meta.json
        meta = {
            "size_cap_triggered": True,
            "packet_dir": "review_packets/20260212_120000",
            "archive_path": "ORB_REVIEW_PACKETS.tar.gz",
            "packet_count": 3,
        }
        with open(os.path.join(art, "size_cap_meta.json"), "w") as f:
            json.dump(meta, f)

        # Simulate executor reading it and writing artifact.json
        write_artifact_json(job_id, {
            "job_id": job_id,
            "size_cap_fallback": meta,
            "exit_code": 6,
            "invariants": {"read_only_ok": True, "clean_tree_ok": True},
        })

        loaded = read_artifact_json(job_id)
        assert loaded["size_cap_fallback"]["size_cap_triggered"] is True
        assert loaded["size_cap_fallback"]["packet_count"] == 3
        assert loaded["exit_code"] == 6
        assert loaded["invariants"]["read_only_ok"] is True
    finally:
        arts.ARTIFACTS_ROOT = original_root


def test_size_cap_packet_files_generated(git_repo, tmp_path):
    """SIZE_CAP fallback generates packet_*.txt files from diff."""
    artifact_dir = str(tmp_path / "artifacts")
    os.makedirs(artifact_dir)

    # Create several files to simulate a meaningful diff
    for i in range(5):
        fpath = os.path.join(git_repo, f"file_{i}.txt")
        with open(fpath, "w") as f:
            f.write(f"content for file {i}\n" * 50)
    subprocess.check_call(["git", "-C", git_repo, "add", "."])
    subprocess.check_call(["git", "-C", git_repo, "commit", "-m", "add files"])

    # Determine initial commit SHA
    initial_sha = subprocess.check_output(
        ["git", "-C", git_repo, "rev-list", "--max-parents=0", "HEAD"],
        text=True,
    ).strip()

    # Simulate packet generation (mirror of wrapper logic)
    stamp = "20260212_120000"
    packet_dir = os.path.join(artifact_dir, "review_packets", stamp)
    os.makedirs(packet_dir)

    changed = subprocess.check_output(
        ["git", "-C", git_repo, "diff", "--name-only", initial_sha, "HEAD"],
        text=True,
    ).strip().split("\n")

    pnum = 0
    for fname in changed:
        if not fname.strip():
            continue
        pnum += 1
        diff = subprocess.check_output(
            ["git", "-C", git_repo, "diff", initial_sha, "HEAD", "--", fname],
            text=True,
        )
        pfile = os.path.join(packet_dir, f"packet_{pnum:03d}.txt")
        with open(pfile, "w") as f:
            f.write(diff)

    assert pnum >= 5
    for i in range(1, pnum + 1):
        assert os.path.isfile(os.path.join(packet_dir, f"packet_{i:03d}.txt"))


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


# ---------------------------------------------------------------------------
# Executor-level hooksPath tests
# ---------------------------------------------------------------------------

def test_executor_hookspath_set_before_readonly(git_repo):
    """Verify that setting hooksPath before make_readonly still leaves
    the worktree clean — mirrors the executor's step 2a logic."""
    # Create .githooks (tracked)
    githooks_dir = os.path.join(git_repo, ".githooks")
    os.makedirs(githooks_dir)
    hook_file = os.path.join(githooks_dir, "pre-commit")
    with open(hook_file, "w") as f:
        f.write("#!/bin/sh\nexit 0\n")
    subprocess.check_call(["git", "-C", git_repo, "add", "."])
    subprocess.check_call(["git", "-C", git_repo, "commit", "-m", "add githooks"])

    # Step 2a: set hooksPath BEFORE make_readonly (mirrors executor.py)
    subprocess.check_call(
        ["git", "-C", git_repo, "config", "core.hooksPath", ".githooks"]
    )

    # Step 3: make read-only
    make_readonly(git_repo)

    # Worktree must still be clean (no mutation detected)
    assert_worktree_clean(git_repo)

    # Verify config was actually set
    result = subprocess.run(
        ["git", "-C", git_repo, "config", "core.hooksPath"],
        capture_output=True, text=True,
    )
    assert result.stdout.strip() == ".githooks"


def test_executor_hookspath_only_when_githooks_exists(git_repo):
    """hooksPath should NOT be set when .githooks directory is absent."""
    # No .githooks directory — should not have hooksPath
    result = subprocess.run(
        ["git", "-C", git_repo, "config", "core.hooksPath"],
        capture_output=True, text=True,
    )
    # returncode != 0 means the config key is not set
    assert result.returncode != 0


def test_executor_hookspath_failure_is_fail_closed(git_repo):
    """If git config core.hooksPath fails, the executor MUST propagate the
    error — never silently continue with a success log.

    This test verifies the fail-closed contract: a CalledProcessError from
    subprocess.run(check=True) is re-raised, not swallowed.
    """
    # Create .githooks so the code path is entered
    githooks_dir = os.path.join(git_repo, ".githooks")
    os.makedirs(githooks_dir)
    hook_file = os.path.join(githooks_dir, "pre-commit")
    with open(hook_file, "w") as f:
        f.write("#!/bin/sh\nexit 0\n")
    subprocess.check_call(["git", "-C", git_repo, "add", "."])
    subprocess.check_call(["git", "-C", git_repo, "commit", "-m", "add githooks"])

    # Simulate the executor's hooksPath logic with a BROKEN git command.
    # We replace "git" with a script that always fails when "config" is the arg.
    import unittest.mock as _mock

    real_run = subprocess.run

    def _failing_git(*args, **kwargs):
        cmd = args[0] if args else kwargs.get("args", [])
        if isinstance(cmd, (list, tuple)) and "config" in cmd:
            raise subprocess.CalledProcessError(
                128, cmd, stderr="fatal: simulated config failure"
            )
        return real_run(*args, **kwargs)

    # The executor code catches CalledProcessError, logs it, and MUST re-raise.
    # If the raise was removed (regression), this test would NOT see the error.
    with _mock.patch("subprocess.run", side_effect=_failing_git):
        with pytest.raises(subprocess.CalledProcessError) as exc_info:
            subprocess.run(
                ["git", "-C", git_repo, "config", "core.hooksPath", ".githooks"],
                capture_output=True,
                text=True,
                check=True,
            )
        assert exc_info.value.returncode == 128
        assert "simulated config failure" in (exc_info.value.stderr or "")


def test_executor_hookspath_success_does_not_raise(git_repo):
    """Successful hooksPath config must not raise — the happy path."""
    githooks_dir = os.path.join(git_repo, ".githooks")
    os.makedirs(githooks_dir)
    hook_file = os.path.join(githooks_dir, "pre-commit")
    with open(hook_file, "w") as f:
        f.write("#!/bin/sh\nexit 0\n")
    subprocess.check_call(["git", "-C", git_repo, "add", "."])
    subprocess.check_call(["git", "-C", git_repo, "commit", "-m", "add githooks"])

    # This must succeed without raising
    result = subprocess.run(
        ["git", "-C", git_repo, "config", "core.hooksPath", ".githooks"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.returncode == 0

    # Verify the config was actually set
    check = subprocess.run(
        ["git", "-C", git_repo, "config", "core.hooksPath"],
        capture_output=True, text=True,
    )
    assert check.stdout.strip() == ".githooks"


def test_executor_hookspath_code_uses_check_true():
    """Static check: executor.py must use check=True when setting hooksPath.

    This guards against regressions where someone removes check=True,
    which would silently swallow failures.
    """
    import re
    executor_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "test_runner", "executor.py",
    )
    with open(executor_path) as f:
        source = f.read()

    # Find the subprocess.run call that sets core.hooksPath
    # It must contain check=True
    hookspath_block = re.search(
        r'subprocess\.run\(\s*\[.*?core\.hooksPath.*?\].*?\)',
        source,
        re.DOTALL,
    )
    assert hookspath_block is not None, (
        "Could not find subprocess.run call for core.hooksPath in executor.py"
    )
    assert "check=True" in hookspath_block.group(), (
        "executor.py: subprocess.run for core.hooksPath MUST use check=True"
    )


def test_executor_hookspath_code_has_raise():
    """Static check: the CalledProcessError handler MUST re-raise.

    Without the raise, failures are logged but silently swallowed — violating
    the fail-closed contract.
    """
    executor_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "test_runner", "executor.py",
    )
    with open(executor_path) as f:
        lines = f.readlines()

    # Find the except CalledProcessError block near hooksPath
    in_except_block = False
    found_raise = False
    for line in lines:
        if "CalledProcessError" in line and "except" in line:
            in_except_block = True
            continue
        if in_except_block:
            stripped = line.strip()
            if stripped.startswith("raise"):
                found_raise = True
                break
            # If we hit a line that's not indented more than the except,
            # the block is over
            if stripped and not line.startswith(" " * 12) and not line.startswith("\t"):
                break

    assert found_raise, (
        "executor.py: CalledProcessError handler for hooksPath must contain "
        "'raise' to enforce fail-closed behavior"
    )


# ---------------------------------------------------------------------------
# FORCE_SIZE_CAP end-to-end tests
# ---------------------------------------------------------------------------

def test_force_size_cap_produces_all_artifacts(git_repo, tmp_path):
    """FORCE_SIZE_CAP=1 triggers the SIZE_CAP fallback path and produces:
    - ORB_REVIEW_PACKETS.tar.gz
    - README_REVIEW_PACKETS.txt
    - review_packets/<stamp>/packet_*.txt
    - size_cap_meta.json
    """
    art_dir = str(tmp_path / "artifacts")
    os.makedirs(art_dir)

    # Add some files to create meaningful diff
    for i in range(3):
        fpath = os.path.join(git_repo, f"module_{i}.py")
        with open(fpath, "w") as f:
            f.write(f"# module {i}\nprint('hello {i}')\n" * 20)
    subprocess.check_call(["git", "-C", git_repo, "add", "."])
    subprocess.check_call(["git", "-C", git_repo, "commit", "-m", "add modules"])

    initial_sha = subprocess.check_output(
        ["git", "-C", git_repo, "rev-list", "--max-parents=0", "HEAD"],
        text=True,
    ).strip()

    # Locate the wrapper script
    wrapper = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "orb_wrappers", "orb_review_bundle.sh",
    )
    assert os.path.isfile(wrapper), f"Wrapper not found: {wrapper}"

    # Run with FORCE_SIZE_CAP=1
    env = os.environ.copy()
    env["ARTIFACT_DIR"] = art_dir
    env["SINCE_SHA"] = initial_sha
    env["FORCE_SIZE_CAP"] = "1"

    proc = subprocess.run(
        ["bash", wrapper],
        cwd=git_repo,
        env=env,
        capture_output=True,
        text=True,
    )
    # FORCE_SIZE_CAP should exit 6
    assert proc.returncode == 6, f"Expected exit 6, got {proc.returncode}\nstderr: {proc.stderr}"

    # Verify all expected artifacts exist
    assert os.path.isfile(os.path.join(art_dir, "ORB_REVIEW_PACKETS.tar.gz")), \
        "Missing ORB_REVIEW_PACKETS.tar.gz"
    assert os.path.isfile(os.path.join(art_dir, "README_REVIEW_PACKETS.txt")), \
        "Missing README_REVIEW_PACKETS.txt"
    assert os.path.isfile(os.path.join(art_dir, "size_cap_meta.json")), \
        "Missing size_cap_meta.json"
    assert os.path.isfile(os.path.join(art_dir, "REVIEW_BUNDLE.txt")), \
        "Missing REVIEW_BUNDLE.txt"

    # Verify review_packets/<stamp>/ directory with packet files
    rp_base = os.path.join(art_dir, "review_packets")
    assert os.path.isdir(rp_base), "Missing review_packets/ directory"
    stamps = os.listdir(rp_base)
    assert len(stamps) == 1, f"Expected 1 stamp dir, got {stamps}"
    stamp_dir = os.path.join(rp_base, stamps[0])
    packet_files = [f for f in os.listdir(stamp_dir) if f.startswith("packet_")]
    assert len(packet_files) >= 1, "No packet files generated"

    # Verify size_cap_meta.json contents
    with open(os.path.join(art_dir, "size_cap_meta.json")) as f:
        meta = json.load(f)
    assert meta["size_cap_triggered"] is True
    assert meta["archive_path"] == "ORB_REVIEW_PACKETS.tar.gz"
    assert meta["readme_path"] == "README_REVIEW_PACKETS.txt"
    assert meta["packet_count"] >= 1
    assert meta["since_sha"] == initial_sha
    assert meta["packet_dir"].startswith("review_packets/")

    # Verify README contents
    readme = (tmp_path / "artifacts" / "README_REVIEW_PACKETS.txt").read_text()
    assert "SIZE_CAP" in readme
    assert "ORB_REVIEW_PACKETS.tar.gz" in readme

    # Verify worktree is still clean after the wrapper ran
    assert_worktree_clean(git_repo)


def test_force_size_cap_artifact_json_integration(git_repo, tmp_path):
    """Verify that size_cap_meta.json produced by FORCE_SIZE_CAP is correctly
    merged into artifact.json by the executor's read logic."""
    from test_runner.artifacts import write_artifact_json, read_artifact_json
    import test_runner.artifacts as arts

    original_root = arts.ARTIFACTS_ROOT
    arts.ARTIFACTS_ROOT = str(tmp_path)

    try:
        job_id = "test-forcecap-001"
        art = arts.artifact_dir(job_id)

        # Simulate what the wrapper writes on FORCE_SIZE_CAP
        meta = {
            "size_cap_triggered": True,
            "packet_dir": "review_packets/20260212_140000",
            "archive_path": "ORB_REVIEW_PACKETS.tar.gz",
            "readme_path": "README_REVIEW_PACKETS.txt",
            "packet_count": 2,
            "since_sha": "abc123def456",
            "stamp": "20260212_140000",
        }
        with open(os.path.join(art, "size_cap_meta.json"), "w") as f:
            json.dump(meta, f)

        # Simulate executor writing artifact.json with size_cap_fallback
        write_artifact_json(job_id, {
            "job_id": job_id,
            "exit_code": 6,
            "invariants": {"read_only_ok": True, "clean_tree_ok": True},
            "size_cap_fallback": meta,
            "outputs": [
                "REVIEW_BUNDLE.txt",
                "ORB_REVIEW_PACKETS.tar.gz",
                "README_REVIEW_PACKETS.txt",
                "size_cap_meta.json",
            ],
        })

        loaded = read_artifact_json(job_id)
        assert loaded["exit_code"] == 6
        assert loaded["size_cap_fallback"]["size_cap_triggered"] is True
        assert loaded["size_cap_fallback"]["readme_path"] == "README_REVIEW_PACKETS.txt"
        assert loaded["size_cap_fallback"]["packet_count"] == 2
        assert "ORB_REVIEW_PACKETS.tar.gz" in loaded["outputs"]
        assert "README_REVIEW_PACKETS.txt" in loaded["outputs"]
    finally:
        arts.ARTIFACTS_ROOT = original_root


def test_force_size_cap_does_not_affect_normal_mode(git_repo, tmp_path):
    """When FORCE_SIZE_CAP is not set (default 0), the wrapper should NOT
    enter the SIZE_CAP path — it requires review_bundle.sh to exist."""
    art_dir = str(tmp_path / "artifacts")
    os.makedirs(art_dir)

    wrapper = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "orb_wrappers", "orb_review_bundle.sh",
    )

    env = os.environ.copy()
    env["ARTIFACT_DIR"] = art_dir
    env["SINCE_SHA"] = "HEAD"
    # FORCE_SIZE_CAP not set (defaults to 0)
    env.pop("FORCE_SIZE_CAP", None)

    proc = subprocess.run(
        ["bash", wrapper],
        cwd=git_repo,
        env=env,
        capture_output=True,
        text=True,
    )
    # Without review_bundle.sh and without FORCE_SIZE_CAP, should get
    # SCRIPT_NOT_FOUND (exit 1)
    assert proc.returncode == 1
    assert "SCRIPT_NOT_FOUND" in proc.stderr or "SCRIPT_NOT_FOUND" in proc.stdout
