"""Shared utilities for the test runner."""

from __future__ import annotations

import hashlib
import os
import socket
import uuid
from datetime import datetime, timezone


def new_job_id() -> str:
    """Return a new unique job id (UUID4 hex)."""
    return uuid.uuid4().hex


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.isoformat()


def hostname() -> str:
    return socket.gethostname()


def new_trace_id() -> str:
    return uuid.uuid4().hex


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)
