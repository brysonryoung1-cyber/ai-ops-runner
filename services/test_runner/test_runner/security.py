"""Security enforcement: read-only worktrees, push-disabled mirrors, clean checks."""

from __future__ import annotations

import logging
import os
import stat
import subprocess

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Push-disable enforcement
# ---------------------------------------------------------------------------

def enforce_push_disabled(git_dir: str) -> None:
    """Set push URL to DISABLED so `git push` always fails."""
    subprocess.check_call(
        ["git", "--git-dir", git_dir,
         "remote", "set-url", "--push", "origin", "DISABLED"],
    )
    # Verify
    result = subprocess.run(
        ["git", "--git-dir", git_dir, "remote", "get-url", "--push", "origin"],
        capture_output=True, text=True,
    )
    if result.stdout.strip() != "DISABLED":
        raise RuntimeError(
            f"Push URL not DISABLED for {git_dir}: {result.stdout.strip()}"
        )
    log.info("Push disabled for %s", git_dir)


def verify_push_disabled(git_dir: str) -> bool:
    """Return True if push URL is 'DISABLED'."""
    result = subprocess.run(
        ["git", "--git-dir", git_dir, "remote", "get-url", "--push", "origin"],
        capture_output=True, text=True,
    )
    return result.stdout.strip() == "DISABLED"


# ---------------------------------------------------------------------------
# Read-only worktree
# ---------------------------------------------------------------------------

def make_readonly(path: str) -> None:
    """Recursively remove write bits, preserving execute bits."""
    for root, dirs, files in os.walk(path):
        for name in dirs + files:
            full = os.path.join(root, name)
            try:
                st = os.stat(full, follow_symlinks=False)
                if stat.S_ISLNK(st.st_mode):
                    continue
                new_mode = st.st_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
                os.chmod(full, new_mode)
            except OSError:
                pass


def make_writable(path: str) -> None:
    """Restore write bits (needed before cleanup)."""
    for root, dirs, files in os.walk(path):
        for name in dirs + files:
            full = os.path.join(root, name)
            try:
                st = os.stat(full, follow_symlinks=False)
                if stat.S_ISLNK(st.st_mode):
                    continue
                new_mode = st.st_mode | stat.S_IWUSR
                os.chmod(full, new_mode)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Clean-tree assertion
# ---------------------------------------------------------------------------

def assert_worktree_clean(worktree_path: str) -> None:
    """Raise if the worktree has any uncommitted changes."""
    porcelain = subprocess.run(
        ["git", "-C", worktree_path, "status", "--porcelain"],
        capture_output=True, text=True,
    )
    if porcelain.stdout.strip():
        raise RuntimeError(
            f"Worktree dirty (status --porcelain): {porcelain.stdout.strip()}"
        )
    diff = subprocess.run(
        ["git", "-C", worktree_path, "diff", "--exit-code"],
        capture_output=True, text=True,
    )
    if diff.returncode != 0:
        raise RuntimeError(
            f"Worktree dirty (git diff): {diff.stdout[:500]}"
        )
