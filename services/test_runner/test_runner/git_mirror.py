"""Git bare-mirror management with push-disabled enforcement."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess

from .security import enforce_push_disabled, make_writable

log = logging.getLogger(__name__)

REPOS_ROOT = os.environ.get("REPOS_ROOT", "/repos")
WORK_ROOT = os.environ.get("WORK_ROOT", "/work")


def mirror_dir(repo_name: str) -> str:
    return os.path.join(REPOS_ROOT, f"{repo_name}.git")


def ensure_mirror(repo_name: str, remote_url: str) -> str:
    """Clone --mirror if missing, then fetch --prune.

    Always enforces push URL = DISABLED.
    Returns the bare git dir path.
    """
    gd = mirror_dir(repo_name)
    if not os.path.isdir(gd):
        log.info("Cloning mirror %s -> %s", remote_url, gd)
        subprocess.check_call(
            ["git", "clone", "--mirror", remote_url, gd],
        )
    else:
        log.info("Fetching mirror %s", gd)
        subprocess.check_call(
            ["git", "--git-dir", gd, "fetch", "--prune"],
        )
    enforce_push_disabled(gd)
    return gd


def create_worktree(repo_name: str, sha: str, job_id: str) -> str:
    """Create a detached worktree for the given SHA.

    Returns the worktree path.
    """
    gd = mirror_dir(repo_name)
    wt = os.path.join(WORK_ROOT, job_id, "repo")
    os.makedirs(os.path.dirname(wt), exist_ok=True)
    subprocess.check_call(
        ["git", "--git-dir", gd, "worktree", "add", "--detach", wt, sha],
    )
    return wt


def remove_worktree(repo_name: str, worktree_path: str) -> None:
    """Remove a worktree (make writable first, then prune)."""
    gd = mirror_dir(repo_name)
    # Restore write bits so we can delete
    make_writable(worktree_path)
    shutil.rmtree(worktree_path, ignore_errors=True)
    # Also remove parent /work/<job_id> if empty
    parent = os.path.dirname(worktree_path)
    if os.path.isdir(parent) and not os.listdir(parent):
        os.rmdir(parent)
    subprocess.run(
        ["git", "--git-dir", gd, "worktree", "prune"],
        capture_output=True,
    )
