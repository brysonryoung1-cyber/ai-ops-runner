"""Background worker – pulls jobs from Redis and executes them."""

from __future__ import annotations

import logging
import signal
import sys
import time

from .db import dequeue_job, get_pg, get_redis, update_job, get_job
from .executor import execute_job
from .util import iso, now_utc

log = logging.getLogger(__name__)

_shutdown = False


def _handle_signal(signum, frame):
    global _shutdown
    log.info("Received signal %s, shutting down gracefully…", signum)
    _shutdown = True


def run_worker() -> None:
    """Main worker loop."""
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log.info("Worker starting")

    r = get_redis()
    pg = get_pg()

    while not _shutdown:
        job_id = dequeue_job(r, timeout=5)
        if job_id is None:
            continue

        log.info("Dequeued job %s", job_id)
        job = get_job(pg, job_id)
        if job is None:
            log.error("Job %s not found in DB, skipping", job_id)
            continue

        # Mark running
        update_job(
            pg, job_id,
            status="running",
            started_at=iso(now_utc()),
        )

        try:
            job = execute_job(job)
        except Exception:
            log.exception("Unhandled error in job %s", job_id)
            job.status = "error"
            job.exit_code = -1
            job.finished_at = iso(now_utc())

        # Persist final state
        update_job(
            pg, job_id,
            status=job.status.value if hasattr(job.status, "value") else job.status,
            started_at=job.started_at,
            finished_at=job.finished_at,
            duration_ms=job.duration_ms,
            exit_code=job.exit_code,
            hostname=job.hostname,
            trace_id=job.trace_id,
            input_hash=job.input_hash,
            allowlist_hash=job.allowlist_hash,
            artifact_dir=job.artifact_dir,
        )
        log.info("Job %s finished: %s", job_id, job.status)

    log.info("Worker stopped")


if __name__ == "__main__":
    run_worker()
