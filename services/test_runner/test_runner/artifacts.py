"""Artifact management – every job writes provenance to artifact.json."""

from __future__ import annotations

import json
import os
from typing import Any

ARTIFACTS_ROOT = os.environ.get("ARTIFACTS_ROOT", "/artifacts")


def artifact_dir(job_id: str) -> str:
    d = os.path.join(ARTIFACTS_ROOT, job_id)
    os.makedirs(d, exist_ok=True)
    return d


def write_artifact_json(job_id: str, data: dict[str, Any]) -> str:
    """Write artifact.json and return its path."""
    d = artifact_dir(job_id)
    path = os.path.join(d, "artifact.json")
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)
    return path


def read_artifact_json(job_id: str) -> dict[str, Any]:
    path = os.path.join(artifact_dir(job_id), "artifact.json")
    with open(path) as f:
        return json.load(f)


REQUIRED_KEYS = frozenset({
    "job_id", "repo_name", "remote_url", "sha", "job_type", "argv",
    "timeout_sec", "started_at", "finished_at", "duration_ms",
    "exit_code", "status", "hostname", "trace_id", "input_hash",
    "allowlist_hash",
})


def validate_artifact(data: dict[str, Any]) -> list[str]:
    """Return list of missing required keys."""
    return sorted(REQUIRED_KEYS - set(data.keys()))
