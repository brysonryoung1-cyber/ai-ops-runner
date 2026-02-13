"""Unit tests for ops/openai_key.py — secure OpenAI API key manager.

Tests verify:
  - Key from env var works
  - Key never appears in stderr
  - Key never appears in stdout for status/default mode
  - Fail-closed when key missing on all platforms
  - keyring-based key retrieval/storage (mocked keyring)
  - Key is NEVER passed via subprocess argv (no subprocess for keyring ops)
  - Linux secrets file (mocked)
  - Interactive prompt stores to keyring (mocked)
  - --emit-env mode (output guard)
  - CLI subcommands: set, delete, status
  - Public API: get_openai_api_key, set_openai_api_key, delete_openai_api_key, openai_key_status
  - _mask_key function
"""

import importlib.util
import os
import subprocess
import sys
import urllib.error
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
# keyring (mocked — never calls real Keychain / SecretService)
# ===========================================================================


class TestKeyringLookup:
    def test_found_in_keyring(self):
        with mock.patch.object(openai_key, "_HAS_KEYRING", True):
            with mock.patch.object(openai_key, "keyring") as mock_kr:
                mock_kr.get_password.return_value = f"  {FAKE_KEY}  \n"
                result = openai_key.get_from_keyring()
                assert result == FAKE_KEY
                mock_kr.get_password.assert_called_once_with(
                    openai_key.SERVICE_NAME, openai_key.ACCOUNT_NAME
                )

    def test_not_found_in_keyring(self):
        with mock.patch.object(openai_key, "_HAS_KEYRING", True):
            with mock.patch.object(openai_key, "keyring") as mock_kr:
                mock_kr.get_password.return_value = None
                assert openai_key.get_from_keyring() is None

    def test_keyring_exception(self):
        with mock.patch.object(openai_key, "_HAS_KEYRING", True):
            with mock.patch.object(openai_key, "keyring") as mock_kr:
                mock_kr.get_password.side_effect = Exception("backend error")
                assert openai_key.get_from_keyring() is None

    def test_keyring_not_available(self):
        with mock.patch.object(openai_key, "_HAS_KEYRING", False):
            assert openai_key.get_from_keyring() is None


class TestKeyringStore:
    def test_store_succeeds(self):
        with mock.patch.object(openai_key, "_HAS_KEYRING", True):
            with mock.patch.object(openai_key, "keyring") as mock_kr:
                assert openai_key.store_in_keyring(FAKE_KEY) is True
                mock_kr.set_password.assert_called_once_with(
                    openai_key.SERVICE_NAME, openai_key.ACCOUNT_NAME, FAKE_KEY
                )

    def test_store_fails(self):
        with mock.patch.object(openai_key, "_HAS_KEYRING", True):
            with mock.patch.object(openai_key, "keyring") as mock_kr:
                mock_kr.set_password.side_effect = Exception("write error")
                assert openai_key.store_in_keyring(FAKE_KEY) is False

    def test_store_without_keyring(self):
        with mock.patch.object(openai_key, "_HAS_KEYRING", False):
            assert openai_key.store_in_keyring(FAKE_KEY) is False


# ===========================================================================
# No-subprocess guard: keyring ops must NEVER spawn subprocesses
# ===========================================================================


class TestNoSubprocessForKeyring:
    """Ensure keyring operations never use subprocess (no argv leak possible)."""

    def test_get_keyring_no_subprocess(self):
        with mock.patch.object(openai_key, "_HAS_KEYRING", True):
            with mock.patch.object(openai_key, "keyring") as mock_kr:
                mock_kr.get_password.return_value = FAKE_KEY
                with mock.patch("subprocess.run") as mock_run:
                    openai_key.get_from_keyring()
                    mock_run.assert_not_called()

    def test_store_keyring_no_subprocess(self):
        with mock.patch.object(openai_key, "_HAS_KEYRING", True):
            with mock.patch.object(openai_key, "keyring") as mock_kr:
                with mock.patch("subprocess.run") as mock_run:
                    openai_key.store_in_keyring(FAKE_KEY)
                    mock_run.assert_not_called()

    def test_e2e_no_subprocess_argv_contains_secret(self):
        """End-to-end: --emit-env via keyring, verify no subprocess leaks."""
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(
                openai_key, "get_from_keyring", return_value=FAKE_KEY
            ):
                with mock.patch("subprocess.run") as mock_run:
                    with mock.patch.object(
                        sys.stdout, "isatty", return_value=False
                    ):
                        openai_key.main(["--emit-env"])
                    for call in mock_run.call_args_list:
                        argv = (
                            call[0][0]
                            if call[0]
                            else call[1].get("args", [])
                        )
                        for arg in argv:
                            assert FAKE_KEY not in str(arg), (
                                f"Secret found in subprocess argv: {argv}"
                            )


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
# _mask_key function
# ===========================================================================


class TestMaskKey:
    def test_long_key_masked(self):
        assert openai_key._mask_key(FAKE_KEY) == "sk-…0000"

    def test_short_key_masked(self):
        assert openai_key._mask_key("short") == "***"

    def test_exactly_8_chars(self):
        assert openai_key._mask_key("12345678") == "***"

    def test_9_chars(self):
        result = openai_key._mask_key("123456789")
        assert result == "123…6789"

    def test_key_never_in_mask(self):
        """Masked output must never contain the full key."""
        masked = openai_key._mask_key(FAKE_KEY)
        assert FAKE_KEY not in masked


# ===========================================================================
# Public API
# ===========================================================================


class TestPublicAPI:
    def test_get_openai_api_key_from_env(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": FAKE_KEY}):
            assert openai_key.get_openai_api_key() == FAKE_KEY

    def test_set_openai_api_key_validates_prefix(self):
        with mock.patch.object(openai_key, "_HAS_KEYRING", True):
            with mock.patch.object(openai_key, "keyring") as mock_kr:
                # Valid key
                assert openai_key.set_openai_api_key(FAKE_KEY) is True
                mock_kr.set_password.assert_called()

    def test_set_openai_api_key_rejects_empty(self):
        assert openai_key.set_openai_api_key("") is False

    def test_set_openai_api_key_rejects_bad_prefix(self):
        assert openai_key.set_openai_api_key("bad-key-1234") is False

    def test_delete_openai_api_key_succeeds(self):
        with mock.patch.object(openai_key, "_HAS_KEYRING", True):
            with mock.patch.object(openai_key, "keyring") as mock_kr:
                with mock.patch.object(
                    openai_key, "LINUX_SECRET_PATH", "/nonexistent/path"
                ):
                    result = openai_key.delete_openai_api_key()
                    assert result is True
                    # Called twice: canonical + legacy names
                    assert mock_kr.delete_password.call_count == 2

    def test_openai_key_status_configured(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": FAKE_KEY}):
            status = openai_key.openai_key_status()
            assert "…" in status
            assert FAKE_KEY not in status

    def test_openai_key_status_not_configured(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(openai_key, "get_from_keyring", return_value=None):
                with mock.patch.object(
                    openai_key, "get_from_linux_file", return_value=None
                ):
                    status = openai_key.openai_key_status()
                    assert status == "not configured"

    def test_openai_key_status_unmasked(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": FAKE_KEY}):
            status = openai_key.openai_key_status(masked=False)
            assert status == FAKE_KEY


# ===========================================================================
# main() — default behavior (status, no raw key printing)
# ===========================================================================


class TestMainStatus:
    """main() default (no subcommand) → status (masked output)."""

    def test_default_shows_masked_status(self, capsys):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": FAKE_KEY}):
            rc = openai_key.main()
        assert rc == 0
        out = capsys.readouterr().out
        assert "OpenAI API key:" in out
        assert FAKE_KEY not in out  # raw key NEVER printed
        assert "…" in out  # must be masked

    def test_status_subcommand(self, capsys):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": FAKE_KEY}):
            rc = openai_key.main(["status"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "OpenAI API key:" in out
        assert FAKE_KEY not in out

    def test_status_no_stderr_leak(self, capsys):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": FAKE_KEY}):
            openai_key.main()
        assert FAKE_KEY not in capsys.readouterr().err

    def test_status_no_key_shows_not_configured(self, capsys):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(openai_key, "get_from_keyring", return_value=None):
                with mock.patch.object(
                    openai_key, "get_from_linux_file", return_value=None
                ):
                    rc = openai_key.main(["status"])
        assert rc == 0
        assert "not configured" in capsys.readouterr().out


# ===========================================================================
# main() — --emit-env mode
# ===========================================================================


class TestMainEmitEnvDarwin:
    """--emit-env on macOS with mocked keyring."""

    def test_emit_env_keyring_hit(self, capsys):
        import shlex

        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("platform.system", return_value="Darwin"):
                with mock.patch.object(openai_key, "get_from_env", return_value=None):
                    with mock.patch.object(
                        openai_key, "get_from_keyring", return_value=FAKE_KEY
                    ):
                        with mock.patch.object(
                            sys.stdout, "isatty", return_value=False
                        ):
                            rc = openai_key.main(["--emit-env"])
        assert rc == 0
        assert capsys.readouterr().out.strip() == (
            f"export OPENAI_API_KEY={shlex.quote(FAKE_KEY)}"
        )

    def test_emit_env_keyring_miss_no_tty_fails(self, capsys):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("platform.system", return_value="Darwin"):
                with mock.patch.object(openai_key, "get_from_env", return_value=None):
                    with mock.patch.object(
                        openai_key, "get_from_keyring", return_value=None
                    ):
                        with mock.patch("sys.stdin") as mock_stdin:
                            mock_stdin.isatty.return_value = False
                            with mock.patch.object(
                                sys.stdout, "isatty", return_value=False
                            ):
                                rc = openai_key.main(["--emit-env"])
        assert rc == 1

    def test_emit_env_no_key_fails_without_prompt(self, capsys):
        """--emit-env fails (non-interactive) when env+keyring both miss."""
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(openai_key, "get_from_env", return_value=None):
                with mock.patch.object(
                    openai_key, "get_from_keyring", return_value=None
                ):
                    with mock.patch.object(
                        openai_key, "get_from_linux_file", return_value=None
                    ):
                        with mock.patch.object(
                            sys.stdout, "isatty", return_value=False
                        ):
                            rc = openai_key.main(["--emit-env"])
        assert rc == 1
        captured = capsys.readouterr()
        assert captured.out.strip() == ""
        assert "not found" in captured.err.lower()


class TestMainEmitEnvLinux:
    """--emit-env on Linux with mocked secrets file + keyring."""

    def test_emit_env_keyring_hit_linux(self, capsys):
        import shlex

        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("platform.system", return_value="Linux"):
                with mock.patch.object(openai_key, "get_from_env", return_value=None):
                    with mock.patch.object(
                        openai_key, "get_from_keyring", return_value=FAKE_KEY
                    ):
                        with mock.patch.object(
                            sys.stdout, "isatty", return_value=False
                        ):
                            rc = openai_key.main(["--emit-env"])
        assert rc == 0
        assert capsys.readouterr().out.strip() == (
            f"export OPENAI_API_KEY={shlex.quote(FAKE_KEY)}"
        )

    def test_emit_env_file_hit(self, capsys):
        import shlex

        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("platform.system", return_value="Linux"):
                with mock.patch.object(openai_key, "get_from_env", return_value=None):
                    with mock.patch.object(
                        openai_key, "get_from_keyring", return_value=None
                    ):
                        with mock.patch.object(
                            openai_key,
                            "get_from_linux_file",
                            return_value=FAKE_KEY,
                        ):
                            with mock.patch.object(
                                sys.stdout, "isatty", return_value=False
                            ):
                                rc = openai_key.main(["--emit-env"])
        assert rc == 0
        assert capsys.readouterr().out.strip() == (
            f"export OPENAI_API_KEY={shlex.quote(FAKE_KEY)}"
        )

    def test_emit_env_file_miss_fails_closed(self, capsys):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("platform.system", return_value="Linux"):
                with mock.patch.object(openai_key, "get_from_env", return_value=None):
                    with mock.patch.object(
                        openai_key, "get_from_keyring", return_value=None
                    ):
                        with mock.patch.object(
                            openai_key, "get_from_linux_file", return_value=None
                        ):
                            with mock.patch.object(
                                sys.stdout, "isatty", return_value=False
                            ):
                                rc = openai_key.main(["--emit-env"])
        assert rc == 1
        captured = capsys.readouterr()
        # Must NOT print the key to stdout on failure
        assert captured.out.strip() == ""
        # Must print instructions to stderr
        assert "/etc/ai-ops-runner/secrets" in captured.err

    def test_emit_env_file_miss_no_key_in_stderr(self, capsys):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("platform.system", return_value="Linux"):
                with mock.patch.object(openai_key, "get_from_env", return_value=None):
                    with mock.patch.object(
                        openai_key, "get_from_keyring", return_value=None
                    ):
                        with mock.patch.object(
                            openai_key, "get_from_linux_file", return_value=None
                        ):
                            with mock.patch.object(
                                sys.stdout, "isatty", return_value=False
                            ):
                                openai_key.main(["--emit-env"])
        assert FAKE_KEY not in capsys.readouterr().err


class TestMainNoKeyAnywhere:
    def test_no_key_anywhere_fails_closed(self, capsys):
        """--emit-env fails when no key found on any platform."""
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(openai_key, "get_from_env", return_value=None):
                with mock.patch.object(
                    openai_key, "get_from_keyring", return_value=None
                ):
                    with mock.patch.object(
                        openai_key, "get_from_linux_file", return_value=None
                    ):
                        with mock.patch.object(
                            sys.stdout, "isatty", return_value=False
                        ):
                            rc = openai_key.main(["--emit-env"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "not found" in err.lower()


# ===========================================================================
# --emit-env mode (TTY guard tests)
# ===========================================================================


class TestEmitEnv:
    """Test --emit-env output guard."""

    def test_emit_env_tty_refused(self, capsys):
        """--emit-env is refused when stdout is a TTY."""
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": FAKE_KEY}):
            with mock.patch.object(sys.stdout, "isatty", return_value=True):
                rc = openai_key.main(["--emit-env"])
        assert rc == 1

    def test_emit_env_non_tty_ok(self, capsys):
        """--emit-env outputs export statement when stdout is not a TTY."""
        import shlex

        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": FAKE_KEY}):
            with mock.patch.object(sys.stdout, "isatty", return_value=False):
                rc = openai_key.main(["--emit-env"])
        assert rc == 0
        out = capsys.readouterr().out.strip()
        assert out == f"export OPENAI_API_KEY={shlex.quote(FAKE_KEY)}"

    def test_emit_env_shell_escaping(self, capsys):
        """--emit-env must shell-escape the key to prevent command injection."""
        import shlex

        # Key with shell metacharacters (would be dangerous unescaped in eval)
        evil_key = "sk-test$(rm -rf /)"
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": evil_key}):
            with mock.patch.object(sys.stdout, "isatty", return_value=False):
                rc = openai_key.main(["--emit-env"])
        assert rc == 0
        out = capsys.readouterr().out.strip()
        # Must be safely quoted — the raw $() must NOT appear unquoted
        assert out == f"export OPENAI_API_KEY={shlex.quote(evil_key)}"
        # Double-check: the output must contain single quotes around the value
        assert "'" in out


# ===========================================================================
# CLI subcommands: set, delete
# ===========================================================================


class TestCLISet:
    """Test 'set' subcommand."""

    def test_set_with_valid_key(self, capsys):
        with mock.patch.object(sys.stdin, "isatty", return_value=True):
            with mock.patch("getpass.getpass", return_value=FAKE_KEY):
                with mock.patch.object(openai_key, "_HAS_KEYRING", True):
                    with mock.patch.object(openai_key, "keyring") as mock_kr:
                        rc = openai_key.main(["set"])
        assert rc == 0
        mock_kr.set_password.assert_called()

    def test_set_with_empty_key_fails(self, capsys):
        with mock.patch.object(sys.stdin, "isatty", return_value=True):
            with mock.patch("getpass.getpass", return_value=""):
                rc = openai_key.main(["set"])
        assert rc == 1

    def test_set_with_bad_prefix_fails(self, capsys):
        with mock.patch.object(sys.stdin, "isatty", return_value=True):
            with mock.patch("getpass.getpass", return_value="bad-prefix-key"):
                rc = openai_key.main(["set"])
        assert rc == 1

    def test_set_eof_exits(self, capsys):
        with mock.patch.object(sys.stdin, "isatty", return_value=True):
            with mock.patch("getpass.getpass", side_effect=EOFError):
                rc = openai_key.main(["set"])
        assert rc == 1


class TestCLIDelete:
    """Test 'delete' subcommand."""

    def test_delete_succeeds(self, capsys):
        with mock.patch.object(openai_key, "_HAS_KEYRING", True):
            with mock.patch.object(openai_key, "keyring") as mock_kr:
                with mock.patch.object(
                    openai_key, "LINUX_SECRET_PATH", "/nonexistent/path"
                ):
                    rc = openai_key.main(["delete"])
        assert rc == 0

    def test_delete_no_key_found(self, capsys):
        with mock.patch.object(openai_key, "_HAS_KEYRING", False):
            with mock.patch.object(
                openai_key, "LINUX_SECRET_PATH", "/nonexistent/path"
            ):
                rc = openai_key.main(["delete"])
        assert rc == 0  # idempotent — no key is not an error


# ===========================================================================
# New canonical API: load_openai_api_key, load_openai_api_key_masked,
#                    assert_openai_api_key_valid, openai_key_source
# ===========================================================================


class TestLoadOpenaiApiKey:
    def test_returns_key_from_env(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": FAKE_KEY}):
            assert openai_key.load_openai_api_key() == FAKE_KEY

    def test_raises_when_missing(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(openai_key, "get_from_keyring", return_value=None):
                with mock.patch.object(
                    openai_key, "get_from_linux_file", return_value=None
                ):
                    with pytest.raises(RuntimeError, match="not found"):
                        openai_key.load_openai_api_key()


class TestLoadOpenaiApiKeyMasked:
    def test_returns_masked(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": FAKE_KEY}):
            result = openai_key.load_openai_api_key_masked()
            assert "…" in result
            assert FAKE_KEY not in result

    def test_raises_when_missing(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(openai_key, "get_from_keyring", return_value=None):
                with mock.patch.object(
                    openai_key, "get_from_linux_file", return_value=None
                ):
                    with pytest.raises(RuntimeError):
                        openai_key.load_openai_api_key_masked()


class TestAssertOpenaiApiKeyValid:
    def test_success(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": FAKE_KEY}):
            with mock.patch("urllib.request.urlopen") as mock_urlopen:
                mock_resp = mock.MagicMock()
                mock_resp.read.return_value = b'{"data":[]}'
                mock_resp.__enter__ = mock.MagicMock(return_value=mock_resp)
                mock_resp.__exit__ = mock.MagicMock(return_value=False)
                mock_urlopen.return_value = mock_resp
                # Should not raise
                openai_key.assert_openai_api_key_valid()

    def test_http_error(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": FAKE_KEY}):
            with mock.patch("urllib.request.urlopen") as mock_urlopen:
                err = urllib.error.HTTPError(
                    "https://api.openai.com/v1/models",
                    401,
                    "Unauthorized",
                    {},
                    None,
                )
                err.read = mock.MagicMock(
                    return_value=b'{"error":{"message":"Invalid API key"}}'
                )
                mock_urlopen.side_effect = err
                with pytest.raises(RuntimeError, match="HTTP 401"):
                    openai_key.assert_openai_api_key_valid()

    def test_raises_when_no_key(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(openai_key, "get_from_keyring", return_value=None):
                with mock.patch.object(
                    openai_key, "get_from_linux_file", return_value=None
                ):
                    with pytest.raises(RuntimeError, match="not found"):
                        openai_key.assert_openai_api_key_valid()


class TestOpenaiKeySource:
    def test_env(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": FAKE_KEY}):
            assert openai_key.openai_key_source() == "env"

    def test_keychain(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(
                openai_key, "get_from_keyring", return_value=FAKE_KEY
            ):
                assert openai_key.openai_key_source() == "keychain"

    def test_none(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(openai_key, "get_from_keyring", return_value=None):
                with mock.patch.object(
                    openai_key, "get_from_linux_file", return_value=None
                ):
                    assert openai_key.openai_key_source() == "none"


# ===========================================================================
# CLI doctor subcommand
# ===========================================================================


class TestCLIDoctor:
    def test_doctor_pass(self, capsys):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": FAKE_KEY}):
            with mock.patch.object(openai_key, "assert_openai_api_key_valid"):
                rc = openai_key.main(["doctor"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "PASS" in out
        assert FAKE_KEY not in out

    def test_doctor_fail_api(self, capsys):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": FAKE_KEY}):
            with mock.patch.object(
                openai_key,
                "assert_openai_api_key_valid",
                side_effect=RuntimeError("HTTP 401 — Invalid API key"),
            ):
                rc = openai_key.main(["doctor"])
        assert rc == 1
        out = capsys.readouterr().out
        assert "FAIL" in out
        assert FAKE_KEY not in out

    def test_doctor_no_key(self, capsys):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(openai_key, "get_from_keyring", return_value=None):
                with mock.patch.object(
                    openai_key, "get_from_linux_file", return_value=None
                ):
                    rc = openai_key.main(["doctor"])
        assert rc == 1
        assert "not configured" in capsys.readouterr().out


# ===========================================================================
# Key NEVER printed tests
# ===========================================================================


class TestKeyNeverPrinted:
    """Verify the raw key never appears in stdout for default/status mode."""

    def test_default_mode_no_raw_key(self, capsys):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": FAKE_KEY}):
            openai_key.main()
        out = capsys.readouterr().out
        assert FAKE_KEY not in out

    def test_status_mode_no_raw_key(self, capsys):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": FAKE_KEY}):
            openai_key.main(["status"])
        out = capsys.readouterr().out
        assert FAKE_KEY not in out

    def test_delete_mode_no_raw_key(self, capsys):
        with mock.patch.object(openai_key, "_HAS_KEYRING", False):
            with mock.patch.object(
                openai_key, "LINUX_SECRET_PATH", "/nonexistent/path"
            ):
                openai_key.main(["delete"])
        captured = capsys.readouterr()
        assert FAKE_KEY not in captured.out
        assert FAKE_KEY not in captured.err

    def test_set_mode_no_raw_key(self, capsys):
        with mock.patch.object(sys.stdin, "isatty", return_value=True):
            with mock.patch("getpass.getpass", return_value=FAKE_KEY):
                with mock.patch.object(openai_key, "_HAS_KEYRING", True):
                    with mock.patch.object(openai_key, "keyring"):
                        openai_key.main(["set"])
        captured = capsys.readouterr()
        assert FAKE_KEY not in captured.out
        assert FAKE_KEY not in captured.err


# ===========================================================================
# End-to-end: subprocess invocation (no mocks)
# ===========================================================================


class TestSubprocessInvocation:
    """Run openai_key.py as a subprocess — closest to real usage."""

    def _clean_env(self, **overrides):
        """Build a clean subprocess env: no OPENAI_API_KEY, keyring disabled."""
        env = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
        # Null backend ensures keyring never returns a stored real key
        env["PYTHON_KEYRING_BACKEND"] = "keyring.backends.null.Keyring"
        env.update(overrides)
        return env

    def test_emit_env_e2e(self):
        """--emit-env outputs 'export OPENAI_API_KEY=...' when captured."""
        import shlex

        result = subprocess.run(
            [sys.executable, str(KEY_SCRIPT), "--emit-env"],
            env={**os.environ, "OPENAI_API_KEY": FAKE_KEY},
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == f"export OPENAI_API_KEY={shlex.quote(FAKE_KEY)}"
        assert FAKE_KEY not in result.stderr

    def test_status_e2e(self):
        """status shows masked output, raw key never in stdout."""
        result = subprocess.run(
            [sys.executable, str(KEY_SCRIPT), "status"],
            env={**os.environ, "OPENAI_API_KEY": FAKE_KEY},
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert FAKE_KEY not in result.stdout
        assert "OpenAI API key:" in result.stdout
        assert "…" in result.stdout

    def test_missing_key_emit_env_e2e(self):
        """No env var + null keyring backend → fail-closed (exit non-zero)."""
        env = self._clean_env()
        result = subprocess.run(
            [sys.executable, str(KEY_SCRIPT), "--emit-env"],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
            stdin=subprocess.DEVNULL,
        )
        assert result.returncode != 0, (
            "Expected non-zero exit when key is absent; got 0. "
            "Hint: PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring "
            "should prevent real Keychain from leaking a key."
        )
        # stdout must be empty (no key printed on failure)
        assert result.stdout.strip() == ""
        # stderr must have diagnostic info
        assert len(result.stderr) > 0

    def test_missing_key_status_e2e(self):
        """status with no key → shows 'not configured', exit 0."""
        env = self._clean_env()
        result = subprocess.run(
            [sys.executable, str(KEY_SCRIPT), "status"],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
            stdin=subprocess.DEVNULL,
        )
        assert result.returncode == 0
        assert "not configured" in result.stdout


# ===========================================================================
# Source-code static security assertions
# ===========================================================================


class TestSourceCodeSecurity:
    """Static analysis: the key script must NEVER use patterns that leak secrets."""

    @pytest.fixture(autouse=True)
    def _load_source(self):
        self.source = KEY_SCRIPT.read_text()

    def test_no_add_generic_password(self):
        """security add-generic-password must never appear (argv leak via -w)."""
        assert "add-generic-password" not in self.source

    def test_no_find_generic_password(self):
        """security find-generic-password must never appear (argv leak via -w)."""
        assert "find-generic-password" not in self.source

    def test_no_security_cli(self):
        """No invocation of the 'security' macOS CLI at all."""
        import re
        # Match subprocess-style invocations: ["security" or 'security' as first arg
        assert not re.search(r"""["']security["']""", self.source), (
            "openai_key.py must not invoke the macOS 'security' CLI"
        )

    def test_no_subprocess_run_with_key_var(self):
        """subprocess.run/call/Popen must never receive a variable named *key*."""
        import re
        # Catch patterns like subprocess.run([..., key, ...]) or
        # subprocess.run([..., api_key, ...])
        hits = re.findall(
            r"subprocess\.\w+\([^)]*\bkey\b", self.source, re.DOTALL
        )
        assert not hits, (
            f"Possible secret in subprocess argv: {hits}"
        )

    def test_no_raw_key_print(self):
        """print(key) must not appear in code — raw key must never go to stdout."""
        import re
        lines = self.source.splitlines()
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if stripped == "print(key)":
                assert False, (
                    f"Line {i}: raw key print found: {stripped}"
                )

    def test_no_print_of_key_variable(self):
        """print() must never be called with the raw key variable (except emit)."""
        import re
        lines = self.source.splitlines()
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            # Match print(...key...) but allow the one known-safe pattern
            if re.search(r'\bprint\(.*\bkey\b', stripped):
                # Known safe patterns:
                #   print(f"export OPENAI_API_KEY={shlex.quote(key)}")  — shell-escaped emit
                #   print(f"OpenAI API key: {result}")  — masked status output
                safe_patterns = (
                    'print(f"OpenAI API key: {_mask_key(val)} (source: {source})")',
                    'print("OpenAI API key: not configured")',
                )
                if stripped in safe_patterns:
                    continue
                # Fail on anything else
                assert False, (
                    f"Line {i}: potential key leak via print(): {stripped}"
                )
