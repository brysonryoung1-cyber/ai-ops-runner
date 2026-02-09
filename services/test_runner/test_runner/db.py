"""Postgres + Redis helpers."""

from __future__ import annotations

import json
import os
from typing import Optional

import psycopg2
import psycopg2.extras
import redis

from .models import JobRecord, JobStatus

# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------

def _pg_dsn() -> str:
    return os.environ.get(
        "DATABASE_URL",
        "postgresql://runner:runner@postgres:5432/runner",
    )


def get_pg():
    conn = psycopg2.connect(_pg_dsn())
    conn.autocommit = True
    return conn


def get_redis() -> redis.Redis:
    url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
    return redis.Redis.from_url(url, decode_responses=True)


# ---------------------------------------------------------------------------
# Job CRUD
# ---------------------------------------------------------------------------

def insert_job(conn, job: JobRecord) -> JobRecord:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO jobs (
                job_id, repo_name, remote_url, sha, job_type,
                argv, timeout_sec, status, idempotency_key,
                started_at, finished_at, duration_ms, exit_code,
                hostname, trace_id, input_hash, allowlist_hash,
                artifact_dir, created_at
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s
            )
            ON CONFLICT (idempotency_key) WHERE idempotency_key IS NOT NULL
            DO NOTHING
            RETURNING job_id
            """,
            (
                job.job_id, job.repo_name, job.remote_url, job.sha,
                job.job_type, json.dumps(job.argv), job.timeout_sec,
                job.status.value, job.idempotency_key,
                job.started_at, job.finished_at, job.duration_ms,
                job.exit_code, job.hostname, job.trace_id,
                job.input_hash, job.allowlist_hash,
                job.artifact_dir, job.created_at,
            ),
        )
        row = cur.fetchone()
        if row is None:
            # idempotency conflict – return existing record
            return get_job(conn, job.idempotency_key, by_idempotency=True)  # type: ignore[arg-type]
    return job


def get_job(
    conn, job_id: str, *, by_idempotency: bool = False
) -> Optional[JobRecord]:
    col = "idempotency_key" if by_idempotency else "job_id"
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(f"SELECT * FROM jobs WHERE {col} = %s", (job_id,))
        row = cur.fetchone()
    if row is None:
        return None
    return _row_to_record(row)


def update_job(conn, job_id: str, **fields) -> None:
    sets = ", ".join(f"{k} = %s" for k in fields)
    vals = list(fields.values())
    vals.append(job_id)
    with conn.cursor() as cur:
        cur.execute(f"UPDATE jobs SET {sets} WHERE job_id = %s", vals)


def _row_to_record(row: dict) -> JobRecord:
    argv = row["argv"]
    if isinstance(argv, str):
        argv = json.loads(argv)
    return JobRecord(
        job_id=row["job_id"],
        repo_name=row["repo_name"],
        remote_url=row["remote_url"],
        sha=row["sha"],
        job_type=row["job_type"],
        argv=argv,
        timeout_sec=row["timeout_sec"],
        status=JobStatus(row["status"]),
        idempotency_key=row.get("idempotency_key"),
        started_at=row.get("started_at"),
        finished_at=row.get("finished_at"),
        duration_ms=row.get("duration_ms"),
        exit_code=row.get("exit_code"),
        hostname=row.get("hostname"),
        trace_id=row.get("trace_id"),
        input_hash=row.get("input_hash"),
        allowlist_hash=row.get("allowlist_hash"),
        artifact_dir=row.get("artifact_dir"),
        created_at=row.get("created_at"),
    )


# ---------------------------------------------------------------------------
# Redis queue
# ---------------------------------------------------------------------------

QUEUE_KEY = "runner:jobs"


def enqueue_job(r: redis.Redis, job_id: str) -> None:
    r.rpush(QUEUE_KEY, job_id)


def dequeue_job(r: redis.Redis, timeout: int = 5) -> Optional[str]:
    result = r.blpop(QUEUE_KEY, timeout=timeout)
    if result is None:
        return None
    return result[1]
