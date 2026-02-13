"""Unit tests for ops/openai_key.py — secure OpenAI API key loader.

Tests verify:
  - Key from env var works
  - Key never appears in stderr
  - Fail-closed when key missing on all platforms
  - keyring-based key retrieval/storage (mocked keyring)
  - Key is NEVER passed via subprocess argv (no subprocess for keyring ops)
  - Linux secrets file (mocked)
  - Interactive prompt stores to keyring (mocked)
  - --emit-env mode (output guard)
"""

import importlib.util
import os
import subprocess
import sys
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
        """End-to-end: resolve_key on macOS, verify no subprocess leaks."""
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("platform.system", return_value="Darwin"):
                with mock.patch.object(
                    openai_key, "get_from_keyring", return_value=None
                ):
                    with mock.patch.object(
                        openai_key, "prompt_and_store", return_value=FAKE_KEY
                    ):
                        with mock.patch("subprocess.run") as mock_run:
                            openai_key.main()
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
    """main() on macOS with mocked keyring."""

    def test_keyring_hit(self, capsys):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("platform.system", return_value="Darwin"):
                with mock.patch.object(openai_key, "get_from_env", return_value=None):
                    with mock.patch.object(
                        openai_key, "get_from_keyring", return_value=FAKE_KEY
                    ):
                        rc = openai_key.main()
        assert rc == 0
        assert capsys.readouterr().out.strip() == FAKE_KEY

    def test_keyring_miss_no_tty_fails(self, capsys):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("platform.system", return_value="Darwin"):
                with mock.patch.object(openai_key, "get_from_env", return_value=None):
                    with mock.patch.object(
                        openai_key, "get_from_keyring", return_value=None
                    ):
                        with mock.patch("sys.stdin") as mock_stdin:
                            mock_stdin.isatty.return_value = False
                            rc = openai_key.main()
        assert rc == 1

    def test_keyring_miss_prompt_stores(self, capsys):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("platform.system", return_value="Darwin"):
                with mock.patch.object(openai_key, "get_from_env", return_value=None):
                    with mock.patch.object(
                        openai_key, "get_from_keyring", return_value=None
                    ):
                        with mock.patch.object(
                            openai_key, "prompt_and_store", return_value=FAKE_KEY
                        ):
                            rc = openai_key.main()
        assert rc == 0
        assert capsys.readouterr().out.strip() == FAKE_KEY


class TestMainLinux:
    """main() on Linux with mocked secrets file + keyring."""

    def test_keyring_hit_linux(self, capsys):
        """keyring is checked before the file on Linux too."""
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("platform.system", return_value="Linux"):
                with mock.patch.object(openai_key, "get_from_env", return_value=None):
                    with mock.patch.object(
                        openai_key, "get_from_keyring", return_value=FAKE_KEY
                    ):
                        rc = openai_key.main()
        assert rc == 0
        assert capsys.readouterr().out.strip() == FAKE_KEY

    def test_file_hit(self, capsys):
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
                            rc = openai_key.main()
        assert rc == 0
        assert capsys.readouterr().out.strip() == FAKE_KEY

    def test_file_miss_fails_closed(self, capsys):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("platform.system", return_value="Linux"):
                with mock.patch.object(openai_key, "get_from_env", return_value=None):
                    with mock.patch.object(
                        openai_key, "get_from_keyring", return_value=None
                    ):
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
                        openai_key, "get_from_keyring", return_value=None
                    ):
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
# --emit-env mode
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
        """No env var + null keyring backend → fail-closed (exit non-zero)."""
        env = self._clean_env()
        result = subprocess.run(
            [sys.executable, str(KEY_SCRIPT)],
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

    def test_no_print_of_key_variable(self):
        """print() must never be called with the raw key variable (except emit)."""
        import re
        # The only prints in the script are _err/_info (stderr) or the
        # controlled stdout emit.  Make sure there's no stray print(key).
        # Exclude the two legitimate patterns: print(key) and
        # print(f"export OPENAI_API_KEY={key}")
        lines = self.source.splitlines()
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            # Match print(...key...) but allow the two known-safe patterns
            if re.search(r'\bprint\(.*\bkey\b', stripped):
                # Known safe patterns:
                #   print(key)                                          — stdout capture
                #   print(f"export OPENAI_API_KEY={shlex.quote(key)}")  — shell-escaped emit
                safe_patterns = (
                    "print(key)",
                    'print(f"export OPENAI_API_KEY={shlex.quote(key)}")',
                )
                if stripped in safe_patterns:
                    continue
                # Fail on anything else
                assert False, (
                    f"Line {i}: potential key leak via print(): {stripped}"
                )
