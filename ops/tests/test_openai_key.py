"""Unit tests for ops/openai_key.py — secure OpenAI API key loader.

Tests verify:
  - Key from env var works
  - Key never appears in stderr
  - Fail-closed when key missing on all platforms
  - macOS Keychain lookup/store (mocked)
  - Linux secrets file (mocked)
  - Interactive prompt stores to Keychain (mocked)
"""

import importlib.util
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from unittest import mock

import pytest

# ---------------------------------------------------------------------------
# Import ops/openai_key.py as a module (it's a standalone script, not a pkg)
# ---------------------------------------------------------------------------
OPS_DIR = Path(__file__).resolve().parent.parent
KEY_SCRIPT = OPS_DIR / "openai_key.py"

spec = importlib.util.spec_from_file_location("openai_key", KEY_SCRIPT)
openai_key = importlib.util.module_from_spec(spec)
spec.loader.exec_module(openai_key)

FAKE_KEY = "sk-test-FAKE-000000000000000000000000000000000000"


# ===========================================================================
# Env-var path
# ===========================================================================

class TestEnvVar:
    def test_returns_key_from_env(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": FAKE_KEY}):
            assert openai_key.get_from_env() == FAKE_KEY

    def test_empty_env_returns_none(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            assert openai_key.get_from_env() is None

    def test_whitespace_env_returns_none(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "   "}):
            assert openai_key.get_from_env() is None

    def test_missing_env_returns_none(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            assert openai_key.get_from_env() is None


# ===========================================================================
# macOS Keychain (mocked)
# ===========================================================================

class TestKeychainLookup:
    def test_found_in_keychain(self):
        fake_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=f"  {FAKE_KEY}  \n", stderr=""
        )
        with mock.patch("subprocess.run", return_value=fake_result) as m:
            result = openai_key.get_from_keychain()
            assert result == FAKE_KEY
            m.assert_called_once()

    def test_not_found_in_keychain(self):
        fake_result = subprocess.CompletedProcess(
            args=[], returncode=44, stdout="", stderr="not found"
        )
        with mock.patch("subprocess.run", return_value=fake_result):
            assert openai_key.get_from_keychain() is None

    def test_keychain_timeout(self):
        with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="", timeout=10)):
            assert openai_key.get_from_keychain() is None

    def test_keychain_not_available(self):
        with mock.patch("subprocess.run", side_effect=FileNotFoundError):
            assert openai_key.get_from_keychain() is None


class TestKeychainStore:
    def test_store_succeeds(self):
        fake_ok = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with mock.patch("subprocess.run", return_value=fake_ok):
            assert openai_key.store_in_keychain(FAKE_KEY) is True

    def test_store_fails(self):
        fake_err = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="error"
        )
        with mock.patch("subprocess.run", return_value=fake_err):
            assert openai_key.store_in_keychain(FAKE_KEY) is False


# ===========================================================================
# Linux secrets file (mocked)
# ===========================================================================

class TestLinuxFile:
    def test_reads_key_from_file(self, tmp_path):
        secret_file = tmp_path / "openai_api_key"
        secret_file.write_text(f"  {FAKE_KEY}  \n")
        with mock.patch.object(openai_key, "LINUX_SECRET_PATH", str(secret_file)):
            assert openai_key.get_from_linux_file() == FAKE_KEY

    def test_file_missing(self, tmp_path):
        with mock.patch.object(
            openai_key, "LINUX_SECRET_PATH", str(tmp_path / "nonexistent")
        ):
            assert openai_key.get_from_linux_file() is None

    def test_file_empty(self, tmp_path):
        secret_file = tmp_path / "openai_api_key"
        secret_file.write_text("")
        with mock.patch.object(openai_key, "LINUX_SECRET_PATH", str(secret_file)):
            assert openai_key.get_from_linux_file() is None

    def test_file_permission_error(self, tmp_path):
        with mock.patch.object(openai_key, "LINUX_SECRET_PATH", str(tmp_path / "key")):
            with mock.patch("builtins.open", side_effect=PermissionError("denied")):
                # isfile check must pass first
                with mock.patch("os.path.isfile", return_value=True):
                    assert openai_key.get_from_linux_file() is None


# ===========================================================================
# main() integration (mocked platform)
# ===========================================================================

class TestMainEnvPath:
    """main() should return 0 and print key when env var is set."""

    def test_env_var_prints_key(self, capsys):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": FAKE_KEY}):
            rc = openai_key.main()
        assert rc == 0
        assert capsys.readouterr().out.strip() == FAKE_KEY

    def test_env_var_no_stderr_leak(self, capsys):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": FAKE_KEY}):
            openai_key.main()
        assert FAKE_KEY not in capsys.readouterr().err


class TestMainDarwin:
    """main() on macOS with mocked Keychain."""

    def test_keychain_hit(self, capsys):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("platform.system", return_value="Darwin"):
                with mock.patch.object(openai_key, "get_from_env", return_value=None):
                    with mock.patch.object(
                        openai_key, "get_from_keychain", return_value=FAKE_KEY
                    ):
                        rc = openai_key.main()
        assert rc == 0
        assert capsys.readouterr().out.strip() == FAKE_KEY

    def test_keychain_miss_no_tty_fails(self, capsys):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("platform.system", return_value="Darwin"):
                with mock.patch.object(openai_key, "get_from_env", return_value=None):
                    with mock.patch.object(
                        openai_key, "get_from_keychain", return_value=None
                    ):
                        with mock.patch("sys.stdin") as mock_stdin:
                            mock_stdin.isatty.return_value = False
                            rc = openai_key.main()
        assert rc == 1

    def test_keychain_miss_prompt_stores(self, capsys):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("platform.system", return_value="Darwin"):
                with mock.patch.object(openai_key, "get_from_env", return_value=None):
                    with mock.patch.object(
                        openai_key, "get_from_keychain", return_value=None
                    ):
                        with mock.patch.object(
                            openai_key, "prompt_and_store", return_value=FAKE_KEY
                        ):
                            rc = openai_key.main()
        assert rc == 0
        assert capsys.readouterr().out.strip() == FAKE_KEY


class TestMainLinux:
    """main() on Linux with mocked secrets file."""

    def test_file_hit(self, capsys):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("platform.system", return_value="Linux"):
                with mock.patch.object(openai_key, "get_from_env", return_value=None):
                    with mock.patch.object(
                        openai_key, "get_from_linux_file", return_value=FAKE_KEY
                    ):
                        rc = openai_key.main()
        assert rc == 0
        assert capsys.readouterr().out.strip() == FAKE_KEY

    def test_file_miss_fails_closed(self, capsys):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("platform.system", return_value="Linux"):
                with mock.patch.object(openai_key, "get_from_env", return_value=None):
                    with mock.patch.object(
                        openai_key, "get_from_linux_file", return_value=None
                    ):
                        rc = openai_key.main()
        assert rc == 1
        captured = capsys.readouterr()
        # Must NOT print the key to stdout on failure
        assert captured.out.strip() == ""
        # Must print instructions to stderr
        assert "/etc/ai-ops-runner/secrets" in captured.err

    def test_file_miss_no_key_in_stderr(self, capsys):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("platform.system", return_value="Linux"):
                with mock.patch.object(openai_key, "get_from_env", return_value=None):
                    with mock.patch.object(
                        openai_key, "get_from_linux_file", return_value=None
                    ):
                        openai_key.main()
        assert FAKE_KEY not in capsys.readouterr().err


class TestMainUnsupportedPlatform:
    def test_unsupported_platform_fails(self, capsys):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("platform.system", return_value="FreeBSD"):
                with mock.patch.object(openai_key, "get_from_env", return_value=None):
                    rc = openai_key.main()
        assert rc == 1
        assert "Unsupported platform" in capsys.readouterr().err


# ===========================================================================
# End-to-end: subprocess invocation (no mocks)
# ===========================================================================

class TestSubprocessInvocation:
    """Run openai_key.py as a subprocess — closest to real usage."""

    def test_env_var_e2e(self):
        result = subprocess.run(
            [sys.executable, str(KEY_SCRIPT)],
            env={**os.environ, "OPENAI_API_KEY": FAKE_KEY},
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == FAKE_KEY
        assert FAKE_KEY not in result.stderr

    def test_missing_key_e2e(self):
        env = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
        result = subprocess.run(
            [sys.executable, str(KEY_SCRIPT)],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
            stdin=subprocess.DEVNULL,
        )
        assert result.returncode != 0
        # stdout must be empty (no key printed on failure)
        assert result.stdout.strip() == ""
        # stderr must have diagnostic info
        assert len(result.stderr) > 0
