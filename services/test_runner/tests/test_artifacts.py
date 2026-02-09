"""Tests for artifact.json provenance writing and validation."""

from __future__ import annotations

import json
import os

import pytest

from test_runner.artifacts import (
    REQUIRED_KEYS,
    validate_artifact,
    write_artifact_json,
    read_artifact_json,
)


@pytest.fixture(autouse=True)
def artifacts_root(tmp_path, monkeypatch):
    monkeypatch.setattr("test_runner.artifacts.ARTIFACTS_ROOT", str(tmp_path))
    return tmp_path


def _full_artifact(job_id="test123"):
    return {
        "job_id": job_id,
        "repo_name": "test-repo",
        "remote_url": "https://github.com/test/repo.git",
        "sha": "abc123",
        "job_type": "local_echo",
        "argv": ["bash", "-lc", "echo hello"],
        "timeout_sec": 60,
        "started_at": "2025-01-01T00:00:00Z",
        "finished_at": "2025-01-01T00:00:05Z",
        "duration_ms": 5000,
        "exit_code": 0,
        "status": "success",
        "hostname": "runner-1",
        "trace_id": "trace-abc",
        "input_hash": "hash-in",
        "allowlist_hash": "hash-al",
    }


def test_write_and_read_artifact():
    data = _full_artifact()
    path = write_artifact_json("test123", data)
    assert os.path.isfile(path)
    loaded = read_artifact_json("test123")
    assert loaded["job_id"] == "test123"
    assert loaded["status"] == "success"


def test_all_required_keys_present():
    data = _full_artifact()
    missing = validate_artifact(data)
    assert missing == [], f"Missing keys: {missing}"


def test_missing_keys_detected():
    data = {"job_id": "x", "repo_name": "y"}
    missing = validate_artifact(data)
    assert len(missing) > 0
    assert "sha" in missing
    assert "status" in missing


def test_required_keys_complete():
    """Verify REQUIRED_KEYS matches the spec."""
    expected = {
        "job_id", "repo_name", "remote_url", "sha", "job_type", "argv",
        "timeout_sec", "started_at", "finished_at", "duration_ms",
        "exit_code", "status", "hostname", "trace_id", "input_hash",
        "allowlist_hash",
    }
    assert REQUIRED_KEYS == expected


def test_artifact_json_is_valid_json(artifacts_root):
    data = _full_artifact("json_test")
    path = write_artifact_json("json_test", data)
    with open(path) as f:
        parsed = json.load(f)
    assert parsed["job_id"] == "json_test"
