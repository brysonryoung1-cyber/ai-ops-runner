"""Data models for the test runner."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    FAILURE = "failure"
    ERROR = "error"
    TIMEOUT = "timeout"


@dataclass
class JobRequest:
    repo_name: str
    remote_url: str
    sha: str
    job_type: str
    idempotency_key: Optional[str] = None


@dataclass
class JobRecord:
    job_id: str
    repo_name: str
    remote_url: str
    sha: str
    job_type: str
    argv: list[str]
    timeout_sec: int
    status: JobStatus = JobStatus.QUEUED
    idempotency_key: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    duration_ms: Optional[int] = None
    exit_code: Optional[int] = None
    hostname: Optional[str] = None
    trace_id: Optional[str] = None
    input_hash: Optional[str] = None
    allowlist_hash: Optional[str] = None
    artifact_dir: Optional[str] = None
    created_at: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d
