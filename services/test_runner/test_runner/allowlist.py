"""Allowlist loader – only pre-approved job types may run."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

import yaml

from .util import sha256_bytes

_ALLOWLIST_PATH = os.environ.get(
    "ALLOWLIST_PATH", "/app/configs/job_allowlist.yaml"
)

_cache: Optional[tuple[str, dict[str, "AllowedJob"]]] = None


@dataclass(frozen=True)
class AllowedJob:
    name: str
    argv: list[str]
    timeout_sec: int
    allowed_params: frozenset[str] = frozenset()
    requires_repo_allowlist: bool = False


def load_allowlist(path: str | None = None) -> dict[str, AllowedJob]:
    """Load and cache the allowlist from YAML.

    Returns a mapping of job_type -> AllowedJob.
    """
    global _cache
    path = path or _ALLOWLIST_PATH
    raw = _read(path)
    digest = sha256_bytes(raw)
    if _cache and _cache[0] == digest:
        return _cache[1]
    data = yaml.safe_load(raw)
    jobs: dict[str, AllowedJob] = {}
    for name, spec in data.get("jobs", {}).items():
        jobs[name] = AllowedJob(
            name=name,
            argv=spec["argv"],
            timeout_sec=spec.get("timeout_sec", 600),
            allowed_params=frozenset(spec.get("allowed_params", [])),
            requires_repo_allowlist=spec.get("requires_repo_allowlist", False),
        )
    _cache = (digest, jobs)
    return jobs


def allowlist_hash(path: str | None = None) -> str:
    path = path or _ALLOWLIST_PATH
    return sha256_bytes(_read(path))


def resolve_job(job_type: str, path: str | None = None) -> AllowedJob:
    """Resolve a job_type to its AllowedJob entry.

    Raises ValueError if the job_type is not allowlisted.
    """
    al = load_allowlist(path)
    if job_type not in al:
        raise ValueError(
            f"Job type {job_type!r} not in allowlist. "
            f"Allowed: {sorted(al.keys())}"
        )
    return al[job_type]


def _read(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()
