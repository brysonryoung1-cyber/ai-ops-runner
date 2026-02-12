#!/usr/bin/env python3
"""Securely load OPENAI_API_KEY from platform-appropriate sources.

Resolution order:
  1. Environment variable (already set) — exit immediately.
  2. macOS Keychain (service: ai-ops-runner-openai).
  3. Linux secrets file (/etc/ai-ops-runner/secrets/openai_api_key).
  4. macOS-only: interactive prompt via getpass → store in Keychain.

On success: prints the key to stdout (for shell capture via $()).
On failure: prints diagnostics to stderr and exits non-zero (fail-closed).

NEVER prints the key to stderr. All human-readable messages go to stderr.
"""

import getpass
import os
import platform
import subprocess
import sys

SERVICE_NAME = "ai-ops-runner-openai"
ACCOUNT_NAME = "openai_api_key"
LINUX_SECRET_PATH = "/etc/ai-ops-runner/secrets/openai_api_key"


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
# Source 2: macOS Keychain
# ---------------------------------------------------------------------------

def get_from_keychain() -> "str | None":
    """Retrieve from macOS Keychain. Returns None if not found."""
    try:
        result = subprocess.run(
            [
                "security", "find-generic-password",
                "-s", SERVICE_NAME,
                "-a", ACCOUNT_NAME,
                "-w",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            key = result.stdout.strip()
            return key if key else None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return None


def store_in_keychain(key: str) -> bool:
    """Store key in macOS Keychain. Returns True on success."""
    # Remove existing entry (ignore errors if absent)
    try:
        subprocess.run(
            [
                "security", "delete-generic-password",
                "-s", SERVICE_NAME,
                "-a", ACCOUNT_NAME,
            ],
            capture_output=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    try:
        result = subprocess.run(
            [
                "security", "add-generic-password",
                "-s", SERVICE_NAME,
                "-a", ACCOUNT_NAME,
                "-w", key,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            _err(f"Keychain add-generic-password failed: {result.stderr.strip()}")
            return False
        return True
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        _err(f"Keychain storage failed: {exc}")
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
    """Prompt user via getpass, validate, store in Keychain."""
    if not sys.stdin.isatty():
        _err("OPENAI_API_KEY not set and no TTY available for prompting.")
        _err("Run once interactively, or set OPENAI_API_KEY in your environment.")
        return None

    _info("")
    _info("╔══════════════════════════════════════════════════════════╗")
    _info("║  One-time OpenAI API key setup                          ║")
    _info("║  The key will be stored in macOS Keychain.              ║")
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

    if store_in_keychain(key):
        _info(f"Key stored in macOS Keychain (service: {SERVICE_NAME}).")
        _info("Future runs will load it automatically — no manual export needed.")
    else:
        _info("WARNING: Key NOT stored in Keychain. It will work for this session only.")

    return key


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    # 1. Environment variable — instant path
    key = get_from_env()
    if key:
        print(key)
        return 0

    system = platform.system()

    if system == "Darwin":
        # 2. macOS Keychain
        key = get_from_keychain()
        if key:
            print(key)
            return 0

        # 3. Interactive prompt → Keychain store
        key = prompt_and_store()
        if key:
            print(key)
            return 0

        # Fail-closed
        _err("Could not obtain OpenAI API key.")
        return 1

    elif system == "Linux":
        # 2. Linux secrets file
        key = get_from_linux_file()
        if key:
            print(key)
            return 0

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
        return 1

    else:
        _err(f"Unsupported platform: {system}")
        _err("Set OPENAI_API_KEY environment variable manually.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
