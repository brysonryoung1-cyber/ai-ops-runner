#!/usr/bin/env python3
"""Capture Kajabi Playwright storage_state via headed browser.

Run on aiops-1 (or any host with display/headful). Opens https://app.kajabi.com,
waits for you to log in and land on the dashboard, then saves storage_state to
/tmp/kajabi_storage_state.json and optionally installs to the secrets path.

Usage:
  python3 ops/scripts/kajabi_capture_storage_state.py [--install]
  # Without --install: writes only to /tmp/kajabi_storage_state.json
  # With --install: also copies to /etc/ai-ops-runner/secrets/soma_kajabi/ (requires root)

Requires: pip install playwright && playwright install chromium
No secrets printed to stdout.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

TMP_PATH = "/tmp/kajabi_storage_state.json"
SECRETS_DIR = Path("/etc/ai-ops-runner/secrets/soma_kajabi")
SECRETS_PATH = SECRETS_DIR / "kajabi_storage_state.json"
KAJABI_URL = "https://app.kajabi.com"
# Dashboard typically has /admin or /dashboard in path; wait for navigation away from login
DASHBOARD_INDICATOR = "/admin"


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture Kajabi storage_state (headed browser)")
    parser.add_argument("--install", action="store_true", help="Copy to secrets dir (requires root)")
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright not installed. Run: pip install playwright && playwright install chromium", file=sys.stderr)
        return 1

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(KAJABI_URL, wait_until="domcontentloaded")
        print("Browser opened. Sign in at https://app.kajabi.com and land on the dashboard.", file=sys.stderr)
        print("Waiting for dashboard (URL containing '/admin') or 5 minutes timeout...", file=sys.stderr)
        try:
            page.wait_for_url(lambda u: DASHBOARD_INDICATOR in u.get("href", ""), timeout=300_000)
        except Exception:
            # Fallback: wait for a common dashboard selector or user to press Enter
            print("If already on dashboard, press Enter in this terminal to save state.", file=sys.stderr)
            input()
        context.storage_state(path=TMP_PATH)
        browser.close()

    if not Path(TMP_PATH).exists() or Path(TMP_PATH).stat().st_size == 0:
        print("Storage state file missing or empty.", file=sys.stderr)
        return 1
    print("Saved to " + TMP_PATH, file=sys.stderr)

    if args.install:
        if os.geteuid() != 0:
            print("Run with sudo to install, or run manually:", file=sys.stderr)
            print(f"  sudo mkdir -p {SECRETS_DIR}", file=sys.stderr)
            print(f"  sudo cp {TMP_PATH} {SECRETS_PATH}", file=sys.stderr)
            print(f"  sudo chmod 600 {SECRETS_PATH}", file=sys.stderr)
            print(f"  sudo chown root:root {SECRETS_PATH}", file=sys.stderr)
            return 0
        SECRETS_DIR.mkdir(parents=True, exist_ok=True)
        import shutil
        shutil.copy2(TMP_PATH, SECRETS_PATH)
        SECRETS_PATH.chmod(0o600)
        try:
            os.chown(SECRETS_PATH, 0, 0)
        except (OSError, AttributeError):
            pass
        print("Installed to " + str(SECRETS_PATH), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
