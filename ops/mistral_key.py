#!/usr/bin/env python3
"""Securely manage MISTRAL_API_KEY — never prints the raw key to human-visible output.

Resolution order (for key retrieval):
  1. Environment variable MISTRAL_API_KEY (already set) — use immediately.
  2. Python keyring (macOS Keychain backend / Linux SecretService).
  3. Linux secrets file (/etc/ai-ops-runner/secrets/mistral_api_key).
  — Never prompts interactively.  Use ``set`` subcommand to store a key. —

Public API (importable):
  load_mistral_api_key()           -> str            -- resolve; raises RuntimeError if missing
  load_mistral_api_key_masked()    -> str            -- shows masked (prefix + last 4)
  assert_mistral_api_key_valid()   -> None          -- minimal Mistral API smoke call; raises on failure
  get_mistral_api_key()            -> str | None    -- resolve from all sources (legacy compat)
  set_mistral_api_key(key)         -> bool           -- store in best backend
  delete_mistral_api_key()         -> bool          -- remove from all backends
  mistral_key_status(masked=True)   -> str           -- masked key or "not configured"
  mistral_key_source()             -> str           -- "env" | "keychain" | "openclaw-mount" | "linux-file" | "none"

CLI subcommands:
  python3 mistral_key.py status       -- show source + masked key
  python3 mistral_key.py doctor       -- run minimal Mistral API smoke test; exit nonzero on failure
  python3 mistral_key.py set           -- read key from stdin safely (no echo) and store
  python3 mistral_key.py print-source -- print only source label (env/keychain/linux-file/none) for scripting
  python3 mistral_key.py delete       -- remove stored key from all backends

Canonical Keychain convention (consistent with OpenAI key tooling):
  service: "ai-ops-runner"
  account: "MISTRAL_API_KEY"

Security guarantees:
  - The key NEVER appears in human-visible output (status shows masked only).
  - Doctor does a lightweight authenticated call and reports PASS/FAIL without leaking key.
  - All keyring operations use the Python keyring library (no security CLI calls).
  - All human-readable messages go to stderr.  The key is NEVER written to stderr.
  - Fail-closed: exits non-zero if key cannot be obtained when required.
  - Non-interactive: never prompts for key in automated pipelines.
"""

import argparse
import getpass
import json as _json
import os
import platform
import queue as _queue
import sys
import threading as _threading
import urllib.error
import urllib.request

SERVICE_NAME = "ai-ops-runner"
ACCOUNT_NAME = "MISTRAL_API_KEY"
# Canonical host path (containers mount /etc/ai-ops-runner/secrets → /run/openclaw_secrets)
LINUX_SECRET_PATH = "/etc/ai-ops-runner/secrets/mistral_api_key"
LEGACY_SECRET_PATH = "/opt/ai-ops-runner/secrets/mistral_api_key"
# Container mount on VPS: host /etc/ai-ops-runner/secrets → /run/openclaw_secrets (docker-compose)
OPENCLAW_SECRETS_PATH = "/run/openclaw_secrets/mistral_api_key"
_KEYRING_TIMEOUT = 5  # seconds

# ---------------------------------------------------------------------------
# Keyring import
# ---------------------------------------------------------------------------
try:
    import keyring as _keyring_mod
    _HAS_KEYRING = True
except ImportError:
    _keyring_mod = None  # type: ignore[assignment]
    _HAS_KEYRING = False

keyring = _keyring_mod


def _err(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)


def _info(msg: str) -> None:
    print(msg, file=sys.stderr)


# ---------------------------------------------------------------------------
# Source 1: Environment variable
# ---------------------------------------------------------------------------


def get_from_env() -> "str | None":
    val = os.environ.get("MISTRAL_API_KEY", "").strip()
    return val if val else None


# ---------------------------------------------------------------------------
# Source 2: Python keyring (macOS Keychain / Linux SecretService)
# ---------------------------------------------------------------------------


def _run_with_timeout(fn, *args, timeout=_KEYRING_TIMEOUT):
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
    if not _HAS_KEYRING:
        return None
    try:
        val = _run_with_timeout(keyring.get_password, SERVICE_NAME, ACCOUNT_NAME)
        if val:
            return val.strip()
    except Exception:
        pass
    return None


def store_in_keyring(key: str) -> bool:
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
    if not _HAS_KEYRING:
        return True
    try:
        _run_with_timeout(keyring.delete_password, SERVICE_NAME, ACCOUNT_NAME)
    except Exception:
        pass
    return True


# ---------------------------------------------------------------------------
# Source 3: Linux secrets file (/etc/ai-ops-runner/secrets/)
# ---------------------------------------------------------------------------


def _migrate_legacy_to_etc() -> None:
    """Idempotent: if key exists at legacy /opt path but not at /etc, copy to /etc.
    Ensures perms 0640 and owner 1000:1000 when possible (container expectations).
    """
    if os.path.isfile(LINUX_SECRET_PATH):
        return
    if not os.path.isfile(LEGACY_SECRET_PATH):
        return
    try:
        with open(LEGACY_SECRET_PATH, "r") as fh:
            key = fh.read().strip()
        if not key:
            return
        secret_dir = os.path.dirname(LINUX_SECRET_PATH)
        os.makedirs(secret_dir, mode=0o750, exist_ok=True)
        with open(LINUX_SECRET_PATH, "w") as fh:
            fh.write(key + "\n")
        os.chmod(LINUX_SECRET_PATH, 0o640)
        try:
            os.chown(LINUX_SECRET_PATH, 1000, 1000)
        except (PermissionError, OSError):
            pass
        _info(f"Migrated Mistral key from {LEGACY_SECRET_PATH} to {LINUX_SECRET_PATH}")
    except (PermissionError, OSError):
        pass


def get_from_openclaw_secrets() -> "str | None":
    """Read from /run/openclaw_secrets/mistral_api_key (container mount on VPS)."""
    if not os.path.isfile(OPENCLAW_SECRETS_PATH):
        return None
    try:
        with open(OPENCLAW_SECRETS_PATH, "r") as fh:
            val = fh.read().strip()
        return val if val else None
    except (PermissionError, OSError):
        return None


def get_from_linux_file() -> "str | None":
    """Read from /etc/ai-ops-runner/secrets/mistral_api_key. Migrates from /opt if present."""
    _migrate_legacy_to_etc()
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
    try:
        secret_dir = os.path.dirname(LINUX_SECRET_PATH)
        os.makedirs(secret_dir, mode=0o750, exist_ok=True)
        with open(LINUX_SECRET_PATH, "w") as fh:
            fh.write(key + "\n")
        os.chmod(LINUX_SECRET_PATH, 0o640)
        try:
            os.chown(LINUX_SECRET_PATH, 1000, 1000)
        except (PermissionError, OSError):
            pass
        _info(f"Key stored in {LINUX_SECRET_PATH}")
        return True
    except PermissionError:
        _err(f"Cannot write {LINUX_SECRET_PATH} -- run with sudo")
        return False
    except OSError as exc:
        _err(f"Cannot write {LINUX_SECRET_PATH}: {exc}")
        return False


def _delete_linux_file() -> bool:
    ok = True
    for path in (LINUX_SECRET_PATH, LEGACY_SECRET_PATH):
        if not os.path.isfile(path):
            continue
        try:
            os.remove(path)
            _info(f"Removed {path}")
        except PermissionError:
            _err(f"Cannot remove {path} -- run with sudo")
            ok = False
        except OSError as exc:
            _err(f"Cannot remove {path}: {exc}")
            ok = False
    return ok


# ---------------------------------------------------------------------------
# Key resolution
# ---------------------------------------------------------------------------


def _resolve_with_source() -> "tuple[str | None, str]":
    """Resolve key from all sources. source_label: env, keychain, openclaw-mount, linux-file, none."""
    val = get_from_env()
    if val:
        return val, "env"
    val = get_from_keyring()
    if val:
        return val, "keychain"
    val = get_from_openclaw_secrets()
    if val:
        return val, "openclaw-mount"
    val = get_from_linux_file()
    if val:
        return val, "linux-file"
    return None, "none"


def resolve_key() -> "str | None":
    """Resolve key from all sources. NEVER prompts interactively."""
    val, _ = _resolve_with_source()
    return val


# ---------------------------------------------------------------------------
# Masking helper
# ---------------------------------------------------------------------------


def _mask_key(val: str) -> str:
    if len(val) > 8:
        return val[:4] + "…" + val[-4:]
    return "***"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_mistral_api_key() -> str:
    """Resolve the Mistral API key.  Raises RuntimeError if not found."""
    val = resolve_key()
    if not val:
        raise RuntimeError(
            "MISTRAL_API_KEY not found. Set env var, or run: "
            "python3 ops/mistral_key.py set"
        )
    return val


def load_mistral_api_key_masked() -> str:
    """Return the masked key fingerprint. Raises RuntimeError if no key configured."""
    return _mask_key(load_mistral_api_key())


def assert_mistral_api_key_valid() -> None:
    """Run a minimal Mistral API smoke call.  Raises RuntimeError on failure.

    Uses a lightweight chat completions request. Reports PASS/FAIL without leaking key.
    """
    tok = load_mistral_api_key()
    url = "https://api.mistral.ai/v1/chat/completions"
    payload = _json.dumps({
        "model": "open-mistral-7b",
        "messages": [{"role": "user", "content": "Hi"}],
        "max_tokens": 1,
    })
    req = urllib.request.Request(
        url,
        data=payload.encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {tok}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read(4096)
    except urllib.error.HTTPError as exc:
        body_snippet = ""
        try:
            raw = exc.read().decode("utf-8", errors="replace")[:500]
            try:
                err_obj = _json.loads(raw)
                body_snippet = err_obj.get("message", raw[:200])
            except (ValueError, AttributeError):
                body_snippet = raw[:200]
        except Exception:
            body_snippet = "(unreadable)"
        raise RuntimeError(
            f"Mistral API validation failed: HTTP {exc.code} — {body_snippet}"
        ) from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Mistral API unreachable: {exc.reason}") from None
    except Exception as exc:
        raise RuntimeError(f"Mistral API smoke test error: {exc}") from None


def get_mistral_api_key() -> "str | None":
    """Resolve the Mistral API key from all configured sources. Returns None if not found."""
    return resolve_key()


def set_mistral_api_key(key: str) -> bool:
    """Store key in the best available backend."""
    key = key.strip()
    if not key:
        _err("Empty key provided.")
        return False

    if _HAS_KEYRING:
        if store_in_keyring(key):
            _info(f"Key stored in system keyring (service: {SERVICE_NAME}).")
            return True

    if platform.system() == "Linux":
        return _write_linux_file(key)

    _err("No available backend to store the key.")
    _err("  macOS: install 'keyring' package (pip install keyring)")
    _err("  Linux: run with sudo to write to " + LINUX_SECRET_PATH + " (or run migration from /opt)")
    return False


def delete_mistral_api_key() -> bool:
    """Remove key from all configured backends."""
    success = True
    deleted_any = False
    if _HAS_KEYRING:
        try:
            _run_with_timeout(keyring.delete_password, SERVICE_NAME, ACCOUNT_NAME)
            _info(f"Key removed from system keyring (service: {SERVICE_NAME}).")
            deleted_any = True
        except Exception:
            pass
    if os.path.isfile(LINUX_SECRET_PATH) or os.path.isfile(LEGACY_SECRET_PATH):
        if _delete_linux_file():
            deleted_any = True
        else:
            success = False
    if not deleted_any:
        _info("No stored key found to delete.")
    return success


def mistral_key_status(masked: bool = True) -> str:
    """Return human-readable status. If masked (default), shows masked key or 'not configured'."""
    val, _ = _resolve_with_source()
    if not val:
        return "not configured"
    if masked:
        return _mask_key(val)
    return val


def mistral_key_source() -> str:
    """Return the source label: 'env', 'keychain', 'openclaw-mount', 'linux-file', or 'none'."""
    _, src = _resolve_with_source()
    return src


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(
        description="Securely manage MISTRAL_API_KEY",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("set", help="Store a new API key (interactive or stdin)")
    subparsers.add_parser("delete", help="Remove stored key from all backends")
    subparsers.add_parser("status", help="Show key source + masked status")
    subparsers.add_parser("doctor", help="Run minimal Mistral API smoke test")
    subparsers.add_parser("print-source", help="Print only source label (env/keychain/linux-file/none)")

    args = parser.parse_args([] if argv is None else argv)
    cmd = args.command if args.command else "status"

    if cmd == "print-source":
        src = mistral_key_source()
        print(src)
        return 0

    if cmd == "set":
        if sys.stdin.isatty():
            try:
                new_val = getpass.getpass(prompt="Enter Mistral API key (input hidden): ")
            except (EOFError, KeyboardInterrupt):
                _info("")
                return 1
        else:
            new_val = sys.stdin.read().strip()
            if not new_val:
                _err("No key provided on stdin.")
                return 1
        if set_mistral_api_key(new_val):
            return 0
        return 1

    if cmd == "delete":
        if delete_mistral_api_key():
            _info("Done.")
            return 0
        return 1

    if cmd == "status":
        val, source = _resolve_with_source()
        if val:
            print(f"Mistral API key: {_mask_key(val)} (source: {source})")
        else:
            print("Mistral API key: not configured")
        return 0

    if cmd == "doctor":
        val, source = _resolve_with_source()
        if not val:
            print("Mistral API key: not configured")
            _err("No key found. Run: python3 ops/mistral_key.py set")
            return 1
        print(f"Mistral API key: {_mask_key(val)} (source: {source})")
        _info("Running Mistral API smoke test...")
        try:
            assert_mistral_api_key_valid()
            print("Smoke test: PASS")
            return 0
        except RuntimeError as exc:
            print(f"Smoke test: FAIL — {exc}")
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
