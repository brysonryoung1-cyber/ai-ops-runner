"""Hermetic tests for Soma SMS module.

Tests allowlist validation, rate limiting, command routing.
No network, no Twilio credentials, no side effects.
"""

import json
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from soma_kajabi_sync.sms import (
    _check_rate_limit,
    _load_allowlist,
    _mark_rate_sent,
    get_last_errors,
    handle_inbound_sms,
    is_allowed_sender,
    log_error,
)


@pytest.fixture
def tmp_rate_dir():
    with tempfile.TemporaryDirectory() as d:
        with patch("soma_kajabi_sync.sms._RATE_DIR", Path(d)):
            yield Path(d)


@pytest.fixture
def tmp_error_log():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        fpath = Path(f.name)
    with patch("soma_kajabi_sync.sms._ERROR_LOG_PATH", fpath):
        yield fpath
    fpath.unlink(missing_ok=True)


class TestAllowlist:
    def test_empty_allowlist_denies(self):
        with patch.dict(os.environ, {"SMS_ALLOWLIST": ""}):
            assert not is_allowed_sender("+15551234567")

    def test_matching_number(self):
        with patch.dict(os.environ, {"SMS_ALLOWLIST": "+15551234567,+15559876543"}):
            assert is_allowed_sender("+15551234567")
            assert is_allowed_sender("+15559876543")

    def test_normalized_number(self):
        with patch.dict(os.environ, {"SMS_ALLOWLIST": "+15551234567"}):
            assert is_allowed_sender("5551234567")  # No +1 prefix
            assert is_allowed_sender("(555) 123-4567")  # Formatted

    def test_nonmatching_number(self):
        with patch.dict(os.environ, {"SMS_ALLOWLIST": "+15551234567"}):
            assert not is_allowed_sender("+15550000000")

    def test_load_allowlist_parsing(self):
        with patch.dict(os.environ, {"SMS_ALLOWLIST": "+1555-123-4567, (555) 987-6543"}):
            al = _load_allowlist()
            assert len(al) == 2


class TestRateLimiting:
    def test_first_call_allowed(self, tmp_rate_dir):
        assert _check_rate_limit("test-key", 60) is True

    def test_second_call_within_limit(self, tmp_rate_dir):
        _mark_rate_sent("test-key")
        assert _check_rate_limit("test-key", 60) is False

    def test_call_after_limit(self, tmp_rate_dir):
        _mark_rate_sent("test-key")
        # Manually backdate the stamp
        stamp = tmp_rate_dir / next(tmp_rate_dir.iterdir()).name
        stamp.write_text(str(time.time() - 120))
        assert _check_rate_limit("test-key", 60) is True


class TestErrorLog:
    def test_log_and_retrieve(self, tmp_error_log):
        log_error("Test error 1")
        log_error("Test error 2")
        errors = get_last_errors(5)
        assert len(errors) == 2
        assert errors[0]["message"] == "Test error 1"
        assert errors[1]["message"] == "Test error 2"

    def test_empty_log(self, tmp_error_log):
        errors = get_last_errors(5)
        assert len(errors) == 0

    def test_limit_results(self, tmp_error_log):
        for i in range(10):
            log_error(f"Error {i}")
        errors = get_last_errors(3)
        assert len(errors) == 3
        assert errors[0]["message"] == "Error 7"


class TestInboundCommands:
    def test_unknown_command_rejected(self, tmp_rate_dir):
        """Test that unknown commands are rejected. Patched rate limiter for hermetic test."""
        with patch.dict(os.environ, {"SMS_ALLOWLIST": "+15551234567"}):
            with patch("soma_kajabi_sync.sms.send_sms", return_value={"ok": True}):
                result = handle_inbound_sms("+15551234567", "INVALID_CMD")
                assert result["ok"] is False
                assert result["reason"] == "unknown_command"

    def test_non_allowed_sender_rejected(self, tmp_rate_dir):
        with patch.dict(os.environ, {"SMS_ALLOWLIST": "+15551234567"}):
            result = handle_inbound_sms("+15550000000", "STATUS")
            assert result["ok"] is False
            assert result["reason"] == "not_allowed"

    def test_empty_allowlist_rejects(self, tmp_rate_dir):
        with patch.dict(os.environ, {"SMS_ALLOWLIST": ""}):
            result = handle_inbound_sms("+15551234567", "STATUS")
            assert result["ok"] is False
            assert result["reason"] == "not_allowed"
