"""Tests for read-only worktree and dirty-tree detection."""

from __future__ import annotations

import os
import stat
import subprocess

import pytest

from test_runner.security import (
    assert_worktree_clean,
    make_readonly,
    make_writable,
)


@pytest.fixture
def git_worktree(tmp_path):
    """Create a simple git repo with one commit to use as worktree."""
    repo = str(tmp_path / "repo")
    os.makedirs(repo)
    subprocess.check_call(["git", "init", repo])
    subprocess.check_call(
        ["git", "-C", repo, "config", "user.email", "test@test.com"]
    )
    subprocess.check_call(
        ["git", "-C", repo, "config", "user.name", "Test"]
    )
    # Create a file and commit
    test_file = os.path.join(repo, "hello.sh")
    with open(test_file, "w") as f:
        f.write("#!/bin/bash\necho hello\n")
    os.chmod(test_file, 0o755)
    subprocess.check_call(["git", "-C", repo, "add", "."])
    subprocess.check_call(["git", "-C", repo, "commit", "-m", "init"])
    return repo


def test_make_readonly_removes_write_bits(git_worktree):
    make_readonly(git_worktree)
    test_file = os.path.join(git_worktree, "hello.sh")
    st = os.stat(test_file)
    # No write bits
    assert not (st.st_mode & stat.S_IWUSR)
    assert not (st.st_mode & stat.S_IWGRP)
    assert not (st.st_mode & stat.S_IWOTH)


def test_make_readonly_preserves_execute_bits(git_worktree):
    make_readonly(git_worktree)
    test_file = os.path.join(git_worktree, "hello.sh")
    st = os.stat(test_file)
    # Execute bit preserved
    assert st.st_mode & stat.S_IXUSR


def test_make_writable_restores_write(git_worktree):
    make_readonly(git_worktree)
    make_writable(git_worktree)
    test_file = os.path.join(git_worktree, "hello.sh")
    st = os.stat(test_file)
    assert st.st_mode & stat.S_IWUSR


def test_clean_worktree_passes(git_worktree):
    """Clean worktree should not raise."""
    assert_worktree_clean(git_worktree)


def test_dirty_worktree_status_fails(git_worktree):
    """Creating an untracked file should make status --porcelain non-empty."""
    dirty_file = os.path.join(git_worktree, "dirty.txt")
    with open(dirty_file, "w") as f:
        f.write("dirty")
    with pytest.raises(RuntimeError, match="Worktree dirty"):
        assert_worktree_clean(git_worktree)


def test_modified_file_fails(git_worktree):
    """Modifying a tracked file should trigger git diff failure."""
    test_file = os.path.join(git_worktree, "hello.sh")
    with open(test_file, "a") as f:
        f.write("# modified\n")
    with pytest.raises(RuntimeError, match="Worktree dirty"):
        assert_worktree_clean(git_worktree)
