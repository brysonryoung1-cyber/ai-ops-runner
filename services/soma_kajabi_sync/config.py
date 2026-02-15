"""Configuration and secret management for Soma Kajabi Sync.

Secrets resolution order: env var → macOS Keychain → Linux file.
NEVER prints raw secrets. Fail-closed on missing required secrets.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SECRETS_DIR = Path("/etc/ai-ops-runner/secrets")
ARTIFACTS_ROOT = Path(os.environ.get("SOMA_ARTIFACTS_ROOT", "artifacts/soma"))

# ---------------------------------------------------------------------------
# Secret names + fallback paths
# ---------------------------------------------------------------------------
SECRET_SPECS: dict[str, dict] = {
    "KAJABI_SESSION_TOKEN": {
        "env": "KAJABI_SESSION_TOKEN",
        "keychain": "KAJABI_SESSION_TOKEN",
        "file": SECRETS_DIR / "kajabi_session_token",
    },
    "GMAIL_APP_PASSWORD": {
        "env": "GMAIL_APP_PASSWORD",
        "keychain": "GMAIL_APP_PASSWORD",
        "file": SECRETS_DIR / "gmail_app_password",
    },
    "GMAIL_USER": {
        "env": "GMAIL_USER",
        "keychain": "GMAIL_USER",
        "file": SECRETS_DIR / "gmail_user",
    },
    "TWILIO_ACCOUNT_SID": {
        "env": "TWILIO_ACCOUNT_SID",
        "keychain": "TWILIO_ACCOUNT_SID",
        "file": SECRETS_DIR / "twilio_account_sid",
    },
    "TWILIO_AUTH_TOKEN": {
        "env": "TWILIO_AUTH_TOKEN",
        "keychain": "TWILIO_AUTH_TOKEN",
        "file": SECRETS_DIR / "twilio_auth_token",
    },
    "TWILIO_FROM_NUMBER": {
        "env": "TWILIO_FROM_NUMBER",
        "keychain": "TWILIO_FROM_NUMBER",
        "file": SECRETS_DIR / "twilio_from_number",
    },
    "SMS_ALLOWLIST": {
        "env": "SMS_ALLOWLIST",
        "keychain": "SMS_ALLOWLIST",
        "file": SECRETS_DIR / "sms_allowlist",
    },
}

# ---------------------------------------------------------------------------
# Kajabi product slugs
# ---------------------------------------------------------------------------
KAJABI_PRODUCTS = {
    "Home User Library": "home-user-library",
    "Practitioner Library": "practitioner-library",
}


def load_secret(name: str, required: bool = True) -> Optional[str]:
    """Load a secret by name. Fail-closed if required and missing.

    Resolution order:
      1. Environment variable
      2. macOS Keychain (if available)
      3. Linux file at /etc/ai-ops-runner/secrets/<name>

    Never prints the raw secret value.
    """
    spec = SECRET_SPECS.get(name)
    if not spec:
        if required:
            print(f"ERROR: Unknown secret name: {name}", file=sys.stderr)
            sys.exit(1)
        return None

    # 1. Environment variable
    val = os.environ.get(spec["env"])
    if val:
        return val.strip()

    # 2. macOS Keychain
    try:
        result = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-a", spec["keychain"],
                "-s", "ai-ops-runner",
                "-w",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # 3. Linux file
    file_path = spec["file"]
    if isinstance(file_path, Path) and file_path.is_file():
        text = file_path.read_text().strip()
        if text:
            return text

    if required:
        print(
            f"ERROR: Secret {name} not found "
            f"(checked: env ${spec['env']}, keychain, {spec['file']})",
            file=sys.stderr,
        )
        sys.exit(1)

    return None


def mask_secret(val: str) -> str:
    """Mask a secret for safe logging: show first 4 and last 4 chars."""
    if len(val) <= 10:
        return "***"
    return f"{val[:4]}...{val[-4:]}"


def get_artifacts_dir(run_id: str) -> Path:
    """Return and create the artifacts directory for a run."""
    d = ARTIFACTS_ROOT / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def ensure_secrets_dir() -> None:
    """Verify /etc/ai-ops-runner/secrets/ exists with correct permissions."""
    if not SECRETS_DIR.exists():
        print(
            f"WARN: Secrets directory {SECRETS_DIR} does not exist. "
            "Some operations may fail.",
            file=sys.stderr,
        )
        return

    # Check directory permissions (should be 700)
    mode = oct(SECRETS_DIR.stat().st_mode)[-3:]
    if mode != "700":
        print(
            f"WARN: {SECRETS_DIR} has mode {mode}, expected 700.",
            file=sys.stderr,
        )
