#!/usr/bin/env python3
"""Securely load OPENAI_API_KEY from platform-appropriate sources.

Resolution order:
  1. Environment variable (already set) -- exit immediately.
  2. Python keyring (macOS Keychain backend / Linux SecretService).
  3. Linux secrets file (/etc/ai-ops-runner/secrets/openai_api_key).
  4. Interactive prompt via getpass -> store in keyring (macOS only, TTY required).

Modes:
  Default:    prints key to stdout for shell capture via $().
  --emit-env: prints "export OPENAI_API_KEY=..." (only when stdout is NOT a TTY).

Security guarantees:
  - The key NEVER appears in subprocess arguments, /proc, ps output, or logs.
  - All keyring operations use the Python keyring library (no security CLI calls).
  - All human-readable messages go to stderr. The key is NEVER written to stderr.
  - Fail-closed: exits non-zero if key cannot be obtained.
"""

import argparse
import getpass
import os
import platform
import queue as _queue
import shlex
import sys
import threading as _threading

SERVICE_NAME = "ai-ops-runner-openai"
ACCOUNT_NAME = "openai_api_key"
LINUX_SECRET_PATH = "/etc/ai-ops-runner/secrets/openai_api_key"
_KEYRING_TIMEOUT = 5  # seconds — prevents hang on locked Keychain dialogs

# ---------------------------------------------------------------------------
# Keyring import — required for macOS, optional for Linux headless servers
# ---------------------------------------------------------------------------
try:
    import keyring as _keyring_mod

    _HAS_KEYRING = True
except ImportError:
    _keyring_mod = None  # type: ignore[assignment]
    _HAS_KEYRING = False

# Expose as module-level attribute so tests can mock it with patch.object
keyring = _keyring_mod


def _err(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)


def _info(msg: str) -> None:
    print(msg, file=sys.stderr)


# ---------------------------------------------------------------------------
# Source 1: Environment variable
# ---------------------------------------------------------------------------


def get_from_env() -> "str | None":
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    return key if key else None


# ---------------------------------------------------------------------------
# Source 2: Python keyring (macOS Keychain / Linux SecretService)
# ---------------------------------------------------------------------------


def _run_with_timeout(fn, *args, timeout=_KEYRING_TIMEOUT):
    """Run *fn* in a daemon thread with a timeout.

    Uses a daemon thread so the Python process can exit even if the
    underlying call is blocked (e.g., macOS Keychain dialog).
    """
    q: _queue.Queue = _queue.Queue()

    def _worker() -> None:
        try:
            q.put(("ok", fn(*args)))
        except Exception as exc:
            q.put(("err", exc))

    t = _threading.Thread(target=_worker, daemon=True)
    t.start()
    try:
        status, value = q.get(timeout=timeout)
    except _queue.Empty:
        raise TimeoutError(f"Operation timed out after {timeout}s")
    if status == "err":
        raise value  # type: ignore[misc]
    return value


def get_from_keyring() -> "str | None":
    """Retrieve from system keyring.

    On macOS this uses the Keychain backend.  On Linux it uses
    SecretService (gnome-keyring / KWallet) when available.

    No subprocess calls — the secret NEVER appears in argv or /proc.
    A timeout guard (daemon thread) prevents hangs when the Keychain
    is locked and a system dialog would block the process.
    Returns None if not found, keyring unavailable, or any error/timeout.
    """
    if not _HAS_KEYRING:
        return None
    try:
        key = _run_with_timeout(keyring.get_password, SERVICE_NAME, ACCOUNT_NAME)
        if key:
            return key.strip()
    except Exception:
        pass
    return None


def store_in_keyring(key: str) -> bool:
    """Store key in system keyring.

    Uses the Python keyring library — secret NEVER appears in
    process arguments, /proc, or ``ps`` output.
    A timeout guard (daemon thread) prevents hangs on locked Keychain dialogs.
    """
    if not _HAS_KEYRING:
        _err("keyring library not available. Install: pip install keyring")
        return False
    try:
        _run_with_timeout(keyring.set_password, SERVICE_NAME, ACCOUNT_NAME, key)
        return True
    except TimeoutError:
        _err("keyring storage timed out (Keychain locked?)")
        return False
    except Exception as exc:
        _err(f"keyring storage failed: {exc}")
        return False


# ---------------------------------------------------------------------------
# Source 3: Linux secrets file
# ---------------------------------------------------------------------------


def get_from_linux_file() -> "str | None":
    """Read from /etc/ai-ops-runner/secrets/openai_api_key."""
    if not os.path.isfile(LINUX_SECRET_PATH):
        return None
    try:
        with open(LINUX_SECRET_PATH, "r") as fh:
            key = fh.read().strip()
        return key if key else None
    except PermissionError:
        _err(
            f"Cannot read {LINUX_SECRET_PATH} — check permissions "
            "(should be chmod 600, owned by the running user)"
        )
        return None
    except OSError as exc:
        _err(f"Cannot read {LINUX_SECRET_PATH}: {exc}")
        return None


# ---------------------------------------------------------------------------
# Interactive prompt (macOS only, TTY required)
# ---------------------------------------------------------------------------


def prompt_and_store() -> "str | None":
    """Prompt user via getpass, validate, store in keyring."""
    if not sys.stdin.isatty():
        _err("OPENAI_API_KEY not set and no TTY available for prompting.")
        _err("Run once interactively, or set OPENAI_API_KEY in your environment.")
        return None

    _info("")
    _info("╔══════════════════════════════════════════════════════════╗")
    _info("║  One-time OpenAI API key setup                          ║")
    _info("║  The key will be stored in the system keyring.          ║")
    _info("║  You will not be asked again on this machine.           ║")
    _info("╚══════════════════════════════════════════════════════════╝")
    _info("")

    try:
        key = getpass.getpass(prompt="OPENAI_API_KEY (input hidden): ")
    except (EOFError, KeyboardInterrupt):
        _info("")
        return None

    key = key.strip()
    if not key:
        _err("Empty key provided.")
        return None

    # Basic format check (OpenAI keys start with sk-)
    if not key.startswith("sk-"):
        _err("Key does not look like a valid OpenAI API key (expected sk-… prefix).")
        return None

    if store_in_keyring(key):
        _info(f"Key stored in system keyring (service: {SERVICE_NAME}).")
        _info("Future runs will load it automatically — no manual export needed.")
    else:
        _info("WARNING: Key NOT stored in keyring. It will work for this session only.")

    return key


# ---------------------------------------------------------------------------
# Key resolution (single function for all platforms)
# ---------------------------------------------------------------------------


def resolve_key() -> "str | None":
    """Resolve key from all sources in priority order.  Returns key or None."""
    # 1. Environment variable — instant path
    key = get_from_env()
    if key:
        return key

    system = platform.system()

    if system == "Darwin":
        # 2. keyring (macOS Keychain)
        key = get_from_keyring()
        if key:
            return key

        # 3. Interactive prompt -> keyring store
        key = prompt_and_store()
        if key:
            return key

        _err("Could not obtain OpenAI API key.")
        return None

    elif system == "Linux":
        # 2. keyring (if SecretService available on desktop Linux)
        key = get_from_keyring()
        if key:
            return key

        # 3. Linux secrets file
        key = get_from_linux_file()
        if key:
            return key

        # Fail-closed with setup instructions
        _err("OPENAI_API_KEY not found.")
        _err("")
        _err("To set up on this Linux machine (one-time):")
        _err("  sudo mkdir -p /etc/ai-ops-runner/secrets")
        _err(
            "  sudo sh -c 'cat > /etc/ai-ops-runner/secrets/openai_api_key'  "
            "# paste key, then Ctrl-D"
        )
        _err("  sudo chmod 600 /etc/ai-ops-runner/secrets/openai_api_key")
        _err(
            "  sudo chown $(whoami):$(id -gn) "
            "/etc/ai-ops-runner/secrets/openai_api_key"
        )
        _err("")
        _err("Never paste your API key into chat. Only enter it directly on the machine.")
        return None

    else:
        _err(f"Unsupported platform: {system}")
        _err("Set OPENAI_API_KEY environment variable manually.")
        return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: "list[str] | None" = None) -> int:
    """Entry point.

    Parameters
    ----------
    argv : list[str] | None
        Command-line arguments.  ``None`` → default (no flags).
        When invoked via ``__main__``, ``sys.argv[1:]`` is passed explicitly.
    """
    parser = argparse.ArgumentParser(
        description="Securely load OPENAI_API_KEY",
    )
    parser.add_argument(
        "--emit-env",
        action="store_true",
        help=(
            "Print 'export OPENAI_API_KEY=...' to stdout. "
            "Refused when stdout is a TTY (safety guard)."
        ),
    )
    args = parser.parse_args([] if argv is None else argv)

    key = resolve_key()
    if not key:
        return 1

    if args.emit_env:
        # --emit-env: only safe when stdout is piped (not visible in terminal)
        if sys.stdout.isatty():
            _err(
                "--emit-env refused: stdout is a TTY. "
                'Use: eval "$(python3 openai_key.py --emit-env)"'
            )
            return 1
        # Shell-escape the key to prevent command injection when used
        # with eval "$(...)".  OpenAI keys are typically safe ASCII, but
        # defense-in-depth: shlex.quote wraps in single quotes.
        print(f"export OPENAI_API_KEY={shlex.quote(key)}")
    else:
        # Default mode: print key for shell capture via $()
        # (stdout is a pipe when captured — key is not visible to humans)
        print(key)

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
