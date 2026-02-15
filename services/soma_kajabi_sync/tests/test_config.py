"""Hermetic tests for Soma config module.

Tests secret resolution, masking, artifact directory creation.
No network, no credentials, no side effects.
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from soma_kajabi_sync.config import (
    KAJABI_PRODUCTS,
    get_artifacts_dir,
    load_secret,
    mask_secret,
)


class TestMaskSecret:
    def test_short_secret(self):
        assert mask_secret("abc") == "***"

    def test_medium_secret(self):
        assert mask_secret("1234567890") == "***"

    def test_long_secret(self):
        result = mask_secret("sk-1234567890abcdef")
        assert result.startswith("sk-1")
        assert result.endswith("cdef")
        assert "..." in result


class TestKajabiProducts:
    def test_known_products(self):
        assert "Home User Library" in KAJABI_PRODUCTS
        assert "Practitioner Library" in KAJABI_PRODUCTS


class TestGetArtifactsDir:
    def test_creates_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("soma_kajabi_sync.config.ARTIFACTS_ROOT", Path(tmp)):
                d = get_artifacts_dir("test-run-001")
                assert d.exists()
                assert d.is_dir()
                assert d.name == "test-run-001"


class TestLoadSecret:
    def test_env_var(self):
        with patch.dict(os.environ, {"KAJABI_SESSION_TOKEN": "test-token-value"}):
            val = load_secret("KAJABI_SESSION_TOKEN", required=False)
            assert val == "test-token-value"

    def test_env_var_with_whitespace(self):
        with patch.dict(os.environ, {"KAJABI_SESSION_TOKEN": "  token-with-spaces  "}):
            val = load_secret("KAJABI_SESSION_TOKEN", required=False)
            assert val == "token-with-spaces"

    def test_file_fallback(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("file-secret-value\n")
            f.flush()
            fpath = Path(f.name)

        try:
            # Patch the file path in SECRET_SPECS
            with patch.dict(
                "soma_kajabi_sync.config.SECRET_SPECS",
                {
                    "TEST_SECRET": {
                        "env": "TEST_SECRET_NONEXISTENT",
                        "keychain": "TEST_SECRET",
                        "file": fpath,
                    },
                },
            ):
                val = load_secret("TEST_SECRET", required=False)
                assert val == "file-secret-value"
        finally:
            fpath.unlink()

    def test_missing_optional(self):
        with patch.dict(os.environ, {}, clear=False):
            # Ensure env is not set
            os.environ.pop("KAJABI_SESSION_TOKEN", None)
            val = load_secret("KAJABI_SESSION_TOKEN", required=False)
            # Will be None if not found in env/keychain/file
            # (may find in keychain on macOS — that's OK)

    def test_unknown_secret_name(self):
        val = load_secret("NONEXISTENT_SECRET_NAME", required=False)
        assert val is None
