#!/usr/bin/env python3
"""Manage the OpenClaw Console authentication token.

Stores a cryptographically random token in macOS Keychain for API auth.

Keychain convention:
  service: "ai-ops-runner"
  account: "OPENCLAW_CONSOLE_TOKEN"

Commands:
  status  — show masked token fingerprint (no secrets)
  rotate  — generate and store a new 256-bit token
  _get    — (internal) print raw token for start.sh

Security:
  - Token is a 64-char hex string (32 bytes / 256 bits of entropy).
  - Stored in macOS Keychain only — never on disk.
  - 'status' shows masked fingerprint only.
  - '_get' is internal; used by start.sh to pass token as env var.
"""

import os
import secrets
import subprocess
import sys

SERVICE = "ai-ops-runner"
ACCOUNT = "OPENCLAW_CONSOLE_TOKEN"


def _err(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)


def _info(msg: str) -> None:
    print(msg, file=sys.stderr)


def _mask_token(token: str) -> str:
    """Mask token for safe display: first 4 + ... + last 4."""
    if len(token) > 12:
        return token[:4] + "..." + token[-4:]
    return "****"


def generate_token() -> str:
    """Generate a cryptographically secure 64-char hex token."""
    return secrets.token_hex(32)


def get_token() -> "str | None":
    """Read token from env or Keychain. Returns None if not found."""
    # 1. Check env
    env_val = os.environ.get("OPENCLAW_CONSOLE_TOKEN", "").strip()
    if env_val:
        return env_val

    # 2. Check macOS Keychain
    try:
        result = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-s", SERVICE,
                "-a", ACCOUNT,
                "-w",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    return None


def store_token(token: str) -> bool:
    """Store token in macOS Keychain. Overwrites if exists."""
    # Delete existing entry (ignore errors — may not exist)
    try:
        subprocess.run(
            [
                "security",
                "delete-generic-password",
                "-s", SERVICE,
                "-a", ACCOUNT,
            ],
            capture_output=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    # Add new entry
    try:
        result = subprocess.run(
            [
                "security",
                "add-generic-password",
                "-s", SERVICE,
                "-a", ACCOUNT,
                "-w", token,
                "-U",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        _err(f"Failed to store token in Keychain: {exc}")
        return False


def cmd_status() -> int:
    """Print masked token fingerprint."""
    token = get_token()
    if token:
        source = (
            "env"
            if os.environ.get("OPENCLAW_CONSOLE_TOKEN", "").strip()
            else "keychain"
        )
        print(f"Console token: {_mask_token(token)} (source: {source})")
    else:
        print("Console token: not configured")
        print("Run: python3 ops/openclaw_console_token.py rotate")
    return 0


def cmd_rotate() -> int:
    """Generate and store a new token."""
    token = generate_token()
    if store_token(token):
        _info(f"Token rotated. Fingerprint: {_mask_token(token)}")
        _info("Restart the console for the new token to take effect:")
        _info("  ./ops/openclaw_console_stop.sh && ./ops/openclaw_console_start.sh")
        return 0
    _err("Failed to store token in Keychain.")
    return 1


def cmd_get_internal() -> int:
    """Internal: print raw token for start.sh to capture as env var."""
    token = get_token()
    if token:
        print(token)
        return 0
    return 1


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: openclaw_console_token.py <status|rotate>")
        return 1

    cmd = sys.argv[1]
    if cmd == "status":
        return cmd_status()
    elif cmd == "rotate":
        return cmd_rotate()
    elif cmd == "_get":
        return cmd_get_internal()
    else:
        _err(f"Unknown command: {cmd}")
        print("Usage: openclaw_console_token.py <status|rotate>")
        return 1


if __name__ == "__main__":
    sys.exit(main())
