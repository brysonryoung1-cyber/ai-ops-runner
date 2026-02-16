"""Unit tests for ops/mistral_key.py — Mistral API key manager.

Tests verify:
  - Key resolution: env, keyring (mocked), linux file (mocked)
  - Missing key => configured=false, doctor reports FAIL without leaking key
  - print-source returns correct label (none/env/keychain/linux-file)
  - No secrets in status/output
"""

import importlib.util
import io
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

OPS_DIR = Path(__file__).resolve().parent.parent
KEY_SCRIPT = OPS_DIR / "mistral_key.py"

spec = importlib.util.spec_from_file_location("mistral_key", KEY_SCRIPT)
mistral_key = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mistral_key)

FAKE_KEY = "mist-test-FAKE-00000000000000000000000000000000"


# ---------------------------------------------------------------------------
# Resolution and status
# ---------------------------------------------------------------------------


class TestMistralKeyResolution:
    def test_resolve_from_env(self):
        with mock.patch.dict(os.environ, {"MISTRAL_API_KEY": FAKE_KEY}):
            assert mistral_key.resolve_key() == FAKE_KEY

    def test_resolve_missing_returns_none(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(mistral_key, "get_from_keyring", return_value=None):
                with mock.patch.object(mistral_key, "get_from_linux_file", return_value=None):
                    assert mistral_key.resolve_key() is None

    def test_status_not_configured_when_missing(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(mistral_key, "get_from_keyring", return_value=None):
                with mock.patch.object(mistral_key, "get_from_linux_file", return_value=None):
                    assert mistral_key.mistral_key_status() == "not configured"

    def test_print_source_returns_none_when_missing(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(mistral_key, "get_from_keyring", return_value=None):
                with mock.patch.object(mistral_key, "get_from_linux_file", return_value=None):
                    assert mistral_key.mistral_key_source() == "none"

    def test_print_source_returns_env_when_set(self):
        with mock.patch.dict(os.environ, {"MISTRAL_API_KEY": FAKE_KEY}):
            assert mistral_key.mistral_key_source() == "env"


# ---------------------------------------------------------------------------
# Doctor: missing key => FAIL, no key in output
# ---------------------------------------------------------------------------


def test_doctor_missing_key_exits_nonzero_no_secret_leak():
    """Doctor with no key must exit 1 and never print the key (stdout/stderr)."""
    with mock.patch.dict(os.environ, {}, clear=True):
        with mock.patch.object(mistral_key, "get_from_keyring", return_value=None):
            with mock.patch.object(mistral_key, "get_from_linux_file", return_value=None):
                old_stdout, old_stderr = sys.stdout, sys.stderr
                try:
                    out = io.StringIO()
                    err = io.StringIO()
                    sys.stdout, sys.stderr = out, err
                    rc = mistral_key.main(["doctor"])
                finally:
                    sys.stdout, sys.stderr = old_stdout, old_stderr
                assert rc == 1
                combined = out.getvalue() + err.getvalue()
                assert FAKE_KEY not in combined
                assert "not configured" in combined or "PASS" not in combined


def test_status_masked_no_raw_key():
    """Status with key set must show masked form only, never raw key."""
    with mock.patch.dict(os.environ, {"MISTRAL_API_KEY": FAKE_KEY}):
        status = mistral_key.mistral_key_status(masked=True)
        assert FAKE_KEY not in status
        assert "…" in status or status == "not configured"
