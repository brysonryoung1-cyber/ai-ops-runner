"""Tests for allowlist parsing and enforcement."""

from __future__ import annotations

import os
import tempfile

import pytest
import yaml

# Ensure we can import without connecting to services
os.environ.setdefault("ALLOWLIST_PATH", "/dev/null")

from test_runner.allowlist import AllowedJob, load_allowlist, resolve_job


@pytest.fixture
def allowlist_file(tmp_path):
    data = {
        "jobs": {
            "local_echo": {
                "argv": ["bash", "-lc", "echo hello && uname -a"],
                "timeout_sec": 60,
            },
            "orb_ops_selftests": {
                "argv": ["bash", "-lc", "./ops/tests/review_auto_selftest.sh && ./ops/tests/ship_auto_selftest.sh"],
                "timeout_sec": 1800,
            },
            "orb_review_auto_nopush": {
                "argv": ["bash", "-lc", "./ops/review_auto.sh --no-push"],
                "timeout_sec": 1800,
            },
        }
    }
    f = tmp_path / "allowlist.yaml"
    f.write_text(yaml.dump(data))
    return str(f)


def test_load_allowlist(allowlist_file):
    jobs = load_allowlist(allowlist_file)
    assert "local_echo" in jobs
    assert "orb_ops_selftests" in jobs
    assert "orb_review_auto_nopush" in jobs
    assert len(jobs) == 3


def test_allowed_job_fields(allowlist_file):
    jobs = load_allowlist(allowlist_file)
    echo = jobs["local_echo"]
    assert isinstance(echo, AllowedJob)
    assert echo.name == "local_echo"
    assert echo.argv == ["bash", "-lc", "echo hello && uname -a"]
    assert echo.timeout_sec == 60


def test_resolve_valid_job(allowlist_file):
    job = resolve_job("local_echo", allowlist_file)
    assert job.name == "local_echo"
    assert job.timeout_sec == 60


def test_resolve_invalid_job(allowlist_file):
    with pytest.raises(ValueError, match="not in allowlist"):
        resolve_job("nonexistent_job", allowlist_file)


def test_argv_immutable(allowlist_file):
    """Callers cannot alter argv – AllowedJob is frozen."""
    job = resolve_job("local_echo", allowlist_file)
    with pytest.raises(AttributeError):
        job.argv = ["something", "else"]  # type: ignore[misc]


def test_allowlist_caching(allowlist_file):
    """Second load returns cached result."""
    import test_runner.allowlist as al
    al._cache = None  # reset
    a = load_allowlist(allowlist_file)
    b = load_allowlist(allowlist_file)
    assert a is b  # same dict object from cache
