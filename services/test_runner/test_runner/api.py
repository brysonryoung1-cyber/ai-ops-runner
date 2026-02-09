"""FastAPI application – job submission and status API."""

from __future__ import annotations

import os
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from .allowlist import resolve_job
from .db import enqueue_job, get_pg, get_redis, insert_job, get_job
from .models import JobRecord, JobRequest, JobStatus
from .artifacts import artifact_dir, ARTIFACTS_ROOT
from .util import iso, new_job_id, now_utc

app = FastAPI(title="ai-ops-runner", version="0.1.0")


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------

class SubmitJobRequest(BaseModel):
    repo_name: str
    remote_url: str
    sha: str
    job_type: str
    idempotency_key: Optional[str] = None


class SubmitJobResponse(BaseModel):
    job_id: str
    artifact_dir: str
    status: str


class JobResponse(BaseModel):
    job_id: str
    repo_name: str
    remote_url: str
    sha: str
    job_type: str
    argv: list[str]
    timeout_sec: int
    status: str
    idempotency_key: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    duration_ms: Optional[int] = None
    exit_code: Optional[int] = None
    hostname: Optional[str] = None
    trace_id: Optional[str] = None
    artifact_dir: Optional[str] = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.post("/jobs", response_model=SubmitJobResponse, status_code=201)
def submit_job(req: SubmitJobRequest):
    # Validate against allowlist
    try:
        allowed = resolve_job(req.job_type)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    jid = new_job_id()
    art = artifact_dir(jid)

    record = JobRecord(
        job_id=jid,
        repo_name=req.repo_name,
        remote_url=req.remote_url,
        sha=req.sha,
        job_type=req.job_type,
        argv=allowed.argv,
        timeout_sec=allowed.timeout_sec,
        idempotency_key=req.idempotency_key,
        artifact_dir=art,
        created_at=iso(now_utc()),
    )

    pg = get_pg()
    record = insert_job(pg, record)

    r = get_redis()
    enqueue_job(r, record.job_id)

    return SubmitJobResponse(
        job_id=record.job_id,
        artifact_dir=record.artifact_dir or art,
        status=record.status.value,
    )


@app.get("/jobs/{job_id}", response_model=JobResponse)
def get_job_status(job_id: str):
    pg = get_pg()
    job = get_job(pg, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobResponse(
        job_id=job.job_id,
        repo_name=job.repo_name,
        remote_url=job.remote_url,
        sha=job.sha,
        job_type=job.job_type,
        argv=job.argv,
        timeout_sec=job.timeout_sec,
        status=job.status.value,
        idempotency_key=job.idempotency_key,
        started_at=job.started_at,
        finished_at=job.finished_at,
        duration_ms=job.duration_ms,
        exit_code=job.exit_code,
        hostname=job.hostname,
        trace_id=job.trace_id,
        artifact_dir=job.artifact_dir,
    )


@app.get("/jobs/{job_id}/logs")
def get_job_logs(
    job_id: str,
    stream: str = Query("stdout", regex="^(stdout|stderr)$"),
    tail: int = Query(200, ge=1, le=10000),
):
    log_file = os.path.join(ARTIFACTS_ROOT, job_id, f"{stream}.log")
    if not os.path.isfile(log_file):
        raise HTTPException(status_code=404, detail=f"No {stream} log found")
    with open(log_file) as f:
        lines = f.readlines()
    return {"stream": stream, "lines": lines[-tail:], "total_lines": len(lines)}
