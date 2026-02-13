#!/usr/bin/env python3
"""Securely manage OPENAI_API_KEY — never prints the raw key to human-visible output.

Resolution order (for key retrieval):
  1. Environment variable OPENAI_API_KEY (already set) — use immediately.
  2. Python keyring (macOS Keychain backend / Linux SecretService).
  3. Linux secrets file (/etc/ai-ops-runner/secrets/openai_api_key).
  — Never prompts interactively.  Use ``set`` subcommand to store a key. —

Public API (importable):
  load_openai_api_key()           -> str            -- resolve; raises RuntimeError if missing
  load_openai_api_key_masked()    -> str            -- shows "sk-…abcd" (prefix + last 4)
  assert_openai_api_key_valid()   -> None           -- minimal OpenAI API smoke call; raises on failure
  get_openai_api_key()            -> str | None      -- resolve from all sources (legacy compat)
  set_openai_api_key(key)         -> bool            -- store in best backend
  delete_openai_api_key()         -> bool            -- remove from all backends
  openai_key_status(masked=True)  -> str             -- "sk-…abcd" or "not configured"
  openai_key_source()             -> str             -- "env" | "keychain" | "linux-file" | "none"

CLI subcommands:
  python3 openai_key.py status      -- show source (env/keychain) + masked key
  python3 openai_key.py doctor      -- run minimal OpenAI API smoke test; exit nonzero on failure
  python3 openai_key.py set         -- read key from stdin safely (no echo) and store to Keychain
  python3 openai_key.py delete      -- remove stored key from all backends
  python3 openai_key.py --emit-env  -- print "export OPENAI_API_KEY=..." (pipe only)

Canonical Keychain convention:
  service: "ai-ops-runner"
  account: "OPENAI_API_KEY"

Security guarantees:
  - The key NEVER appears in human-visible output (status shows masked only).
  - --emit-env is refused when stdout is a TTY (safety guard).
  - All keyring operations use the Python keyring library (no security CLI calls).
  - All human-readable messages go to stderr.  The key is NEVER written to stderr.
  - Fail-closed: exits non-zero if key cannot be obtained.
  - Non-interactive: never prompts for key in automated pipelines.
"""

import argparse
import getpass
import json as _json
import os
import platform
import queue as _queue
import shlex
import sys
import threading as _threading
import urllib.error
import urllib.request

SERVICE_NAME = "ai-ops-runner"
ACCOUNT_NAME = "OPENAI_API_KEY"
# Legacy Keychain names — used for one-time migration from old entries
_OLD_SERVICE_NAME = "ai-ops-runner-openai"
_OLD_ACCOUNT_NAME = "openai_api_key"
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
    val = os.environ.get("OPENAI_API_KEY", "").strip()
    return val if val else None


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
    """Retrieve from system keyring (macOS Keychain / Linux SecretService).

    Tries the canonical service/account names first, then legacy names.
    If found under legacy names, migrates to canonical names in-place
    (upsert + delete old).  The key is NEVER printed during migration.

    No subprocess calls — the secret NEVER appears in argv or /proc.
    A timeout guard (daemon thread) prevents hangs when the Keychain
    is locked and a system dialog would block the process.
    Returns None if not found, keyring unavailable, or any error/timeout.
    """
    if not _HAS_KEYRING:
        return None

    # 1. Try canonical names
    try:
        val = _run_with_timeout(keyring.get_password, SERVICE_NAME, ACCOUNT_NAME)
        if val:
            return val.strip()
    except Exception:
        pass

    # 2. Try legacy names for migration
    try:
        val = _run_with_timeout(
            keyring.get_password, _OLD_SERVICE_NAME, _OLD_ACCOUNT_NAME
        )
        if val:
            val = val.strip()
            # Migrate: store under canonical names (upsert)
            try:
                _run_with_timeout(
                    keyring.set_password, SERVICE_NAME, ACCOUNT_NAME, val
                )
                # Remove old entry (best-effort, never fatal)
                try:
                    _run_with_timeout(
                        keyring.delete_password,
                        _OLD_SERVICE_NAME,
                        _OLD_ACCOUNT_NAME,
                    )
                except Exception:
                    pass
                _info(
                    "Migrated Keychain entry to canonical service/account names."
                )
            except Exception:
                pass  # Migration failed — still return the key
            return val
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


def _delete_from_keyring() -> bool:
    """Remove key from system keyring (both canonical and legacy entries).

    Returns True on success or not-found.
    """
    if not _HAS_KEYRING:
        return True
    for svc, acct in [
        (SERVICE_NAME, ACCOUNT_NAME),
        (_OLD_SERVICE_NAME, _OLD_ACCOUNT_NAME),
    ]:
        try:
            _run_with_timeout(keyring.delete_password, svc, acct)
        except Exception:
            pass  # Not found or unavailable — treat as success
    return True


# ---------------------------------------------------------------------------
# Source 3: Linux secrets file
# ---------------------------------------------------------------------------


def get_from_linux_file() -> "str | None":
    """Read from /etc/ai-ops-runner/secrets/openai_api_key."""
    if not os.path.isfile(LINUX_SECRET_PATH):
        return None
    try:
        with open(LINUX_SECRET_PATH, "r") as fh:
            val = fh.read().strip()
        return val if val else None
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
# Interactive prompt (used ONLY by 'set' CLI subcommand, never by resolve)
# ---------------------------------------------------------------------------


def prompt_and_store() -> "str | None":
    """Prompt user via getpass, validate, store in keyring.

    This is called only from the CLI ``set`` command, never from
    the automated resolution chain.
    """
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
        val = getpass.getpass(prompt="OPENAI_API_KEY (input hidden): ")
    except (EOFError, KeyboardInterrupt):
        _info("")
        return None

    val = val.strip()
    if not val:
        _err("Empty key provided.")
        return None

    # Basic format check (OpenAI keys start with sk-)
    if not val.startswith("sk-"):
        _err("Key does not look like a valid OpenAI API key (expected sk-… prefix).")
        return None

    if store_in_keyring(val):
        _info(f"Key stored in system keyring (service: {SERVICE_NAME}).")
        _info("Future runs will load it automatically — no manual export needed.")
    else:
        _info("WARNING: Key NOT stored in keyring. It will work for this session only.")

    return val


# ---------------------------------------------------------------------------
# Key resolution (deterministic, non-interactive)
# ---------------------------------------------------------------------------


def _resolve_with_source() -> "tuple[str | None, str]":
    """Resolve key from all sources and return (key, source_label).

    Sources checked in priority order.  NEVER prompts interactively.
    source_label is one of: "env", "keychain", "linux-file", "none".
    """
    val = get_from_env()
    if val:
        return val, "env"

    val = get_from_keyring()
    if val:
        return val, "keychain"

    val = get_from_linux_file()
    if val:
        return val, "linux-file"

    return None, "none"


def resolve_key() -> "str | None":
    """Resolve key from all sources in priority order.  Returns key or None.

    NEVER prompts interactively.
    """
    val, _source = _resolve_with_source()
    return val


# ---------------------------------------------------------------------------
# Masking helper
# ---------------------------------------------------------------------------


def _mask_key(val: str) -> str:
    """Mask a key for safe display: 'sk-…abcd'."""
    if len(val) > 8:
        return val[:3] + "…" + val[-4:]
    return "***"


# ---------------------------------------------------------------------------
# Public API (importable)
# ---------------------------------------------------------------------------


def load_openai_api_key() -> str:
    """Resolve the OpenAI API key.  Raises RuntimeError if not found.

    Resolution: env OPENAI_API_KEY → Keychain → Linux secrets file.
    Never prompts interactively.
    """
    val = resolve_key()
    if not val:
        raise RuntimeError(
            "OPENAI_API_KEY not found. Set env var, or run: "
            "python3 ops/openai_key.py set"
        )
    return val


def load_openai_api_key_masked() -> str:
    """Return the masked key fingerprint (prefix + last 4).

    Raises RuntimeError if no key is configured.
    """
    return _mask_key(load_openai_api_key())


def assert_openai_api_key_valid() -> None:
    """Run a minimal OpenAI API smoke call.  Raises RuntimeError on failure.

    Uses the /v1/models endpoint (read-only, no cost) to verify the key
    authenticates successfully.
    """
    tok = load_openai_api_key()
    req = urllib.request.Request(
        "https://api.openai.com/v1/models",
        headers={"Authorization": f"Bearer {tok}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            # Read a small amount to confirm valid response
            resp.read(4096)
    except urllib.error.HTTPError as exc:
        body_snippet = ""
        try:
            raw = exc.read().decode("utf-8", errors="replace")[:500]
            try:
                err_obj = _json.loads(raw)
                body_snippet = err_obj.get("error", {}).get("message", raw[:200])
            except (ValueError, AttributeError):
                body_snippet = raw[:200]
        except Exception:
            body_snippet = "(unreadable)"
        raise RuntimeError(
            f"OpenAI API validation failed: HTTP {exc.code} — {body_snippet}"
        ) from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"OpenAI API unreachable: {exc.reason}") from None
    except Exception as exc:
        raise RuntimeError(f"OpenAI API smoke test error: {exc}") from None


def get_openai_api_key() -> "str | None":
    """Resolve the OpenAI API key from all configured sources.

    Returns None if not found.
    For new code prefer load_openai_api_key() which raises on failure.
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
        # Delete canonical entry
        try:
            _run_with_timeout(keyring.delete_password, SERVICE_NAME, ACCOUNT_NAME)
            _info(f"Key removed from system keyring (service: {SERVICE_NAME}).")
            deleted_any = True
        except Exception:
            pass
        # Also clean legacy entry
        try:
            _run_with_timeout(
                keyring.delete_password, _OLD_SERVICE_NAME, _OLD_ACCOUNT_NAME
            )
            deleted_any = True
        except Exception:
            pass

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
    val, _src = _resolve_with_source()
    if not val:
        return "not configured"
    if masked:
        return _mask_key(val)
    return val


def openai_key_source() -> str:
    """Return the source label of the current key.

    One of: 'env', 'keychain', 'linux-file', 'none'.
    """
    _val, src = _resolve_with_source()
    return src


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
    subparsers.add_parser(
        "set", help="Store a new API key (interactive prompt or stdin pipe)"
    )
    subparsers.add_parser("delete", help="Remove stored API key from all backends")
    subparsers.add_parser("status", help="Show key source + masked status")
    subparsers.add_parser("doctor", help="Run minimal OpenAI API smoke test")

    args = parser.parse_args([] if argv is None else argv)

    # --emit-env: controlled stdout emit for shell capture
    if args.emit_env:
        # TTY safety check FIRST — before resolve_key()
        if sys.stdout.isatty():
            _err(
                "--emit-env refused: stdout is a TTY. "
                'Use: eval "$(python3 openai_key.py --emit-env)"'
            )
            return 1
        val = resolve_key()
        if not val:
            _err("OPENAI_API_KEY not found.")
            _info(
                "  Checked: env var, Keychain (keyring), /etc/ai-ops-runner/secrets/"
            )
            _info("  To set up: python3 ops/openai_key.py set")
            _info("  Or: export OPENAI_API_KEY=sk-...")
            return 1
        # Shell-escape the key to prevent command injection when used
        # with eval "$(...)".
        print(f"export OPENAI_API_KEY={shlex.quote(val)}")
        return 0

    # Default to status if no subcommand given
    cmd = args.command if args.command else "status"

    if cmd == "set":
        if sys.stdin.isatty():
            try:
                new_val = getpass.getpass(
                    prompt="Enter OpenAI API key (input hidden): "
                )
            except (EOFError, KeyboardInterrupt):
                _info("")
                return 1
        else:
            # Non-TTY: read from stdin directly (supports piping)
            new_val = sys.stdin.read().strip()
            if not new_val:
                _err("No key provided on stdin.")
                return 1
        if set_openai_api_key(new_val):
            return 0
        return 1

    elif cmd == "delete":
        if delete_openai_api_key():
            _info("Done.")
            return 0
        return 1

    elif cmd == "status":
        val, source = _resolve_with_source()
        if val:
            print(f"OpenAI API key: {_mask_key(val)} (source: {source})")
        else:
            print("OpenAI API key: not configured")
        return 0

    elif cmd == "doctor":
        val, source = _resolve_with_source()
        if not val:
            print("OpenAI API key: not configured")
            _err("No key found. Run: python3 ops/openai_key.py set")
            return 1
        print(f"OpenAI API key: {_mask_key(val)} (source: {source})")
        _info("Running OpenAI API smoke test...")
        try:
            assert_openai_api_key_valid()
            print("Smoke test: PASS")
            return 0
        except RuntimeError as exc:
            print(f"Smoke test: FAIL — {exc}")
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
