"""Tests for git push-disabled enforcement."""

from __future__ import annotations

import os
import subprocess

import pytest

from test_runner.security import (
    enforce_push_disabled,
    verify_push_disabled,
)


@pytest.fixture
def bare_repo(tmp_path):
    """Create a bare git repo with a dummy remote."""
    repo_dir = str(tmp_path / "test.git")
    subprocess.check_call(["git", "init", "--bare", repo_dir])
    # Add a fake origin remote
    subprocess.check_call(
        ["git", "--git-dir", repo_dir, "remote", "add", "origin",
         "https://github.com/test/repo.git"],
    )
    return repo_dir


def test_enforce_push_disabled(bare_repo):
    enforce_push_disabled(bare_repo)
    assert verify_push_disabled(bare_repo)


def test_push_url_is_disabled(bare_repo):
    enforce_push_disabled(bare_repo)
    result = subprocess.run(
        ["git", "--git-dir", bare_repo, "remote", "get-url", "--push", "origin"],
        capture_output=True, text=True,
    )
    assert result.stdout.strip() == "DISABLED"


def test_fetch_url_preserved(bare_repo):
    """Fetch URL should remain the original, only push is disabled."""
    enforce_push_disabled(bare_repo)
    result = subprocess.run(
        ["git", "--git-dir", bare_repo, "remote", "get-url", "origin"],
        capture_output=True, text=True,
    )
    assert result.stdout.strip() == "https://github.com/test/repo.git"


def test_push_would_fail(bare_repo):
    """Attempting git push to DISABLED should fail."""
    enforce_push_disabled(bare_repo)
    result = subprocess.run(
        ["git", "--git-dir", bare_repo, "push", "origin", "main"],
        capture_output=True, text=True,
    )
    # Push to "DISABLED" URL must fail
    assert result.returncode != 0
