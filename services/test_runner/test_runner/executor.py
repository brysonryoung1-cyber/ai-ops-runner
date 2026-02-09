"""Job executor – runs allowlisted commands in an ephemeral worktree."""

from __future__ import annotations

import json as _json
import logging
import os
import subprocess
import time
from typing import Optional

from .allowlist import allowlist_hash, resolve_job
from .artifacts import artifact_dir, write_artifact_json
from .git_mirror import create_worktree, ensure_mirror, remove_worktree
from .models import JobRecord, JobStatus
from .security import assert_worktree_clean, make_readonly
from .util import hostname, iso, new_trace_id, now_utc, sha256_bytes

log = logging.getLogger(__name__)


def _list_outputs(art_dir: str) -> list[str]:
    """List all output files in the artifact directory (relative names)."""
    if not os.path.isdir(art_dir):
        return []
    return sorted(
        f for f in os.listdir(art_dir)
        if os.path.isfile(os.path.join(art_dir, f))
    )


def _load_params(art_dir: str) -> dict[str, str]:
    """Load params.json from artifact dir if present (written by API)."""
    params_path = os.path.join(art_dir, "params.json")
    if os.path.isfile(params_path):
        with open(params_path) as f:
            return _json.load(f)
    return {}


def execute_job(job: JobRecord) -> JobRecord:
    """Run a single job end-to-end (mirror -> worktree -> run -> cleanup)."""
    trace_id = new_trace_id()
    job.trace_id = trace_id
    job.hostname = hostname()
    job.status = JobStatus.RUNNING
    job.started_at = iso(now_utc())
    job.input_hash = sha256_bytes(
        f"{job.repo_name}:{job.remote_url}:{job.sha}:{job.job_type}".encode()
    )
    job.allowlist_hash = allowlist_hash()

    art_dir = artifact_dir(job.job_id)
    job.artifact_dir = art_dir
    stdout_path = os.path.join(art_dir, "stdout.log")
    stderr_path = os.path.join(art_dir, "stderr.log")

    worktree_path: Optional[str] = None
    invariants: dict = {"read_only_ok": False, "clean_tree_ok": False}
    params: dict[str, str] = {}

    try:
        # 1. Ensure mirror
        ensure_mirror(job.repo_name, job.remote_url)

        # 2. Create ephemeral worktree
        worktree_path = create_worktree(job.repo_name, job.sha, job.job_id)

        # 3. Make worktree read-only (preserve execute bits)
        make_readonly(worktree_path)
        invariants["read_only_ok"] = True

        # 4. Resolve allowlisted argv
        allowed = resolve_job(job.job_type)
        job.argv = allowed.argv
        job.timeout_sec = allowed.timeout_sec

        # 5. Load params and build execution environment
        params = _load_params(art_dir)
        env = os.environ.copy()
        env["ARTIFACT_DIR"] = art_dir
        for key, value in params.items():
            if key in allowed.allowed_params:
                env[key.upper()] = str(value)

        # 6. Run the command
        t0 = time.monotonic()
        with open(stdout_path, "w") as fo, open(stderr_path, "w") as fe:
            proc = subprocess.run(
                allowed.argv,
                cwd=worktree_path,
                stdout=fo,
                stderr=fe,
                timeout=allowed.timeout_sec,
                env=env,
            )
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        job.exit_code = proc.returncode
        job.duration_ms = elapsed_ms
        job.status = (
            JobStatus.SUCCESS if proc.returncode == 0 else JobStatus.FAILURE
        )

        # 7. Assert worktree is clean (MUTATION_DETECTED check)
        try:
            assert_worktree_clean(worktree_path)
            invariants["clean_tree_ok"] = True
        except RuntimeError as exc:
            job.status = JobStatus.FAILURE
            invariants["clean_tree_ok"] = False
            invariants["reason"] = "MUTATION_DETECTED"
            invariants["details"] = str(exc)
            # Capture changed files for diagnostics
            changed = subprocess.run(
                ["git", "-C", worktree_path, "status", "--porcelain"],
                capture_output=True, text=True,
            )
            if changed.stdout.strip():
                invariants["changed_files"] = changed.stdout.strip().split("\n")
            log.error("MUTATION_DETECTED in job %s: %s", job.job_id, exc)
            with open(stderr_path, "a") as fe:
                fe.write(f"\n--- MUTATION_DETECTED ---\n{exc}\n")

    except subprocess.TimeoutExpired:
        job.status = JobStatus.TIMEOUT
        job.exit_code = -1
        job.duration_ms = job.timeout_sec * 1000
        log.error("Job %s timed out after %ss", job.job_id, job.timeout_sec)
    except Exception as exc:
        job.status = JobStatus.ERROR
        job.exit_code = -1
        log.exception("Job %s error: %s", job.job_id, exc)
        # Write error details to stderr log
        with open(stderr_path, "a") as fe:
            fe.write(f"\n--- RUNNER ERROR ---\n{exc}\n")
    finally:
        job.finished_at = iso(now_utc())
        if job.duration_ms is None:
            job.duration_ms = 0

        # 8. Collect outputs and write artifact.json
        outputs = _list_outputs(art_dir)
        write_artifact_json(job.job_id, {
            "job_id": job.job_id,
            "repo_name": job.repo_name,
            "remote_url": job.remote_url,
            "sha": job.sha,
            "job_type": job.job_type,
            "argv": job.argv,
            "timeout_sec": job.timeout_sec,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "duration_ms": job.duration_ms,
            "exit_code": job.exit_code,
            "status": job.status.value,
            "hostname": job.hostname,
            "trace_id": job.trace_id,
            "input_hash": job.input_hash,
            "allowlist_hash": job.allowlist_hash,
            "stdout_path": stdout_path,
            "stderr_path": stderr_path,
            "params": params,
            "outputs": outputs,
            "invariants": invariants,
        })

        # 9. Cleanup worktree
        if worktree_path and os.path.isdir(worktree_path):
            try:
                remove_worktree(job.repo_name, worktree_path)
            except Exception:
                log.exception("Failed to remove worktree %s", worktree_path)

    return job
