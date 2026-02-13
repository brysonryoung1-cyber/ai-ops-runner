#!/usr/bin/env python3
"""Securely manage OPENAI_API_KEY — never prints the raw key to human-visible output.

Resolution order (for key retrieval):
  1. Environment variable (already set) -- exit immediately.
  2. Python keyring (macOS Keychain backend / Linux SecretService).
  3. Linux secrets file (/etc/ai-ops-runner/secrets/openai_api_key).
  4. Interactive prompt via getpass -> store in keyring (macOS only, TTY required).

Public API (importable):
  get_openai_api_key()           -> str | None  -- resolve from all sources
  set_openai_api_key(key)        -> bool         -- store in best backend
  delete_openai_api_key()        -> bool         -- remove from all backends
  openai_key_status(masked=True) -> str          -- "sk-...abcd" or "not configured"

CLI subcommands:
  python3 openai_key.py status      -- show masked status (default)
  python3 openai_key.py set         -- prompt and store
  python3 openai_key.py delete      -- remove from all backends
  python3 openai_key.py --emit-env  -- print "export OPENAI_API_KEY=..." (pipe only)

Security guarantees:
  - The key NEVER appears in human-visible output (status shows masked only).
  - --emit-env is refused when stdout is a TTY (safety guard).
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
_KEYRING_TIMEOUT = 5  # seconds -- prevents hang on locked Keychain dialogs

# ---------------------------------------------------------------------------
# Keyring import -- required for macOS, optional for Linux headless servers
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

    No subprocess calls -- the secret NEVER appears in argv or /proc.
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

    Uses the Python keyring library -- secret NEVER appears in
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


def _delete_from_keyring() -> bool:
    """Remove key from system keyring.  Returns True on success or not-found."""
    if not _HAS_KEYRING:
        return True
    try:
        _run_with_timeout(keyring.delete_password, SERVICE_NAME, ACCOUNT_NAME)
        return True
    except Exception:
        return True  # Not found or unavailable -- treat as success


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
            f"Cannot read {LINUX_SECRET_PATH} -- check permissions "
            "(should be chmod 600, owned by the running user)"
        )
        return None
    except OSError as exc:
        _err(f"Cannot read {LINUX_SECRET_PATH}: {exc}")
        return None


def _write_linux_file(key: str) -> bool:
    """Write key to Linux secrets file (/etc/ai-ops-runner/secrets/)."""
    try:
        secret_dir = os.path.dirname(LINUX_SECRET_PATH)
        os.makedirs(secret_dir, mode=0o700, exist_ok=True)
        with open(LINUX_SECRET_PATH, "w") as fh:
            fh.write(key + "\n")
        os.chmod(LINUX_SECRET_PATH, 0o600)
        _info(f"Key stored in {LINUX_SECRET_PATH}")
        return True
    except PermissionError:
        _err(f"Cannot write {LINUX_SECRET_PATH} -- run with sudo")
        return False
    except OSError as exc:
        _err(f"Cannot write {LINUX_SECRET_PATH}: {exc}")
        return False


def _delete_linux_file() -> bool:
    """Remove the Linux secrets file.  Returns True on success or not-found."""
    if not os.path.isfile(LINUX_SECRET_PATH):
        return True
    try:
        os.remove(LINUX_SECRET_PATH)
        return True
    except PermissionError:
        _err(f"Cannot remove {LINUX_SECRET_PATH} -- run with sudo")
        return False
    except OSError as exc:
        _err(f"Cannot remove {LINUX_SECRET_PATH}: {exc}")
        return False


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
    # 1. Environment variable -- instant path
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
# Masking helper
# ---------------------------------------------------------------------------


def _mask_key(key: str) -> str:
    """Mask a key for safe display: 'sk-…abcd'."""
    if len(key) > 8:
        return key[:3] + "…" + key[-4:]
    return "***"


# ---------------------------------------------------------------------------
# Public API (importable)
# ---------------------------------------------------------------------------


def get_openai_api_key() -> "str | None":
    """Resolve the OpenAI API key from all configured sources.

    May trigger an interactive prompt on macOS if no stored key is found.
    """
    return resolve_key()


def set_openai_api_key(key: str) -> bool:
    """Store key in the best available backend.

    Tries keyring first (macOS Keychain / Linux SecretService), then
    Linux secrets file.  Returns True on success.
    """
    key = key.strip()
    if not key:
        _err("Empty key provided.")
        return False
    if not key.startswith("sk-"):
        _err("Key does not look like a valid OpenAI API key (expected sk- prefix).")
        return False

    if _HAS_KEYRING:
        if store_in_keyring(key):
            _info(f"Key stored in system keyring (service: {SERVICE_NAME}).")
            return True

    if platform.system() == "Linux":
        return _write_linux_file(key)

    _err("No available backend to store the key.")
    _err("  macOS: install 'keyring' package (pip install keyring)")
    _err("  Linux: run with sudo to write to " + LINUX_SECRET_PATH)
    return False


def delete_openai_api_key() -> bool:
    """Remove key from all configured backends.  Returns True on success."""
    success = True
    deleted_any = False

    if _HAS_KEYRING:
        try:
            _run_with_timeout(keyring.delete_password, SERVICE_NAME, ACCOUNT_NAME)
            _info(f"Key removed from system keyring (service: {SERVICE_NAME}).")
            deleted_any = True
        except Exception:
            pass  # Not found or backend unavailable

    if os.path.isfile(LINUX_SECRET_PATH):
        if _delete_linux_file():
            _info(f"Key removed from {LINUX_SECRET_PATH}")
            deleted_any = True
        else:
            success = False

    if not deleted_any:
        _info("No stored key found to delete.")

    return success


def openai_key_status(masked: bool = True) -> str:
    """Return human-readable status of the OpenAI API key.

    Checks all sources quietly (no interactive prompts, no error messages).
    Returns 'not configured' if no key found.
    If masked (default), shows 'sk-…abcd' format.
    """
    # Check sources without side effects (no prompts, no error messages)
    key = get_from_env()
    if not key:
        key = get_from_keyring()
    if not key:
        key = get_from_linux_file()
    if not key:
        return "not configured"
    if masked:
        return _mask_key(key)
    return key


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: "list[str] | None" = None) -> int:
    """Entry point.

    Parameters
    ----------
    argv : list[str] | None
        Command-line arguments.  ``None`` -> default (shows status).
        When invoked via ``__main__``, ``sys.argv[1:]`` is passed explicitly.
    """
    parser = argparse.ArgumentParser(
        description="Securely manage OPENAI_API_KEY",
    )
    parser.add_argument(
        "--emit-env",
        action="store_true",
        help=(
            "Print 'export OPENAI_API_KEY=...' to stdout. "
            "Refused when stdout is a TTY (safety guard)."
        ),
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("set", help="Store a new API key (interactive prompt)")
    subparsers.add_parser("delete", help="Remove stored API key from all backends")
    subparsers.add_parser("status", help="Show key status (masked)")

    args = parser.parse_args([] if argv is None else argv)

    # --emit-env: controlled stdout emit for shell capture
    if args.emit_env:
        key = resolve_key()
        if not key:
            return 1
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
        return 0

    # Default to status if no subcommand given
    cmd = args.command if args.command else "status"

    if cmd == "set":
        try:
            new_key = getpass.getpass(prompt="Enter OpenAI API key (input hidden): ")
        except (EOFError, KeyboardInterrupt):
            _info("")
            return 1
        if set_openai_api_key(new_key):
            return 0
        return 1

    elif cmd == "delete":
        if delete_openai_api_key():
            _info("Done.")
            return 0
        return 1

    elif cmd == "status":
        result = openai_key_status()
        print(f"OpenAI API key: {result}")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
