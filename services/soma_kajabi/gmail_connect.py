#!/usr/bin/env python3
"""Gmail connector setup. Provides start/status/finalize for OAuth device flow.

Prints verification_url + user_code (OK to display). Never prints tokens.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.soma_kajabi.connector_config import (
    GMAIL_OAUTH_PATH,
    load_soma_kajabi_config,
    _repo_root,
)


def _run_start() -> int:
    """Start Gmail OAuth device flow. Returns verification_url and user_code (not secrets)."""
    root = _repo_root()
    cfg, err = load_soma_kajabi_config(root)
    if err:
        print(json.dumps({"ok": False, "error_class": err}))
        return 1
    # For imap mode, point user to configure GMAIL_USER + GMAIL_APP_PASSWORD
    mode = cfg.get("gmail", {}).get("mode", "manual")
    if mode == "imap":
        print(json.dumps({
            "ok": True,
            "step": "connect_start",
            "mode": "imap",
            "message": "For IMAP: configure GMAIL_USER and GMAIL_APP_PASSWORD in /etc/ai-ops-runner/secrets/",
        }, indent=2))
        return 0
    if mode == "manual":
        print(json.dumps({
            "ok": True,
            "step": "connect_start",
            "message": "Set gmail.mode to 'imap' or 'oauth' in config, then run connect_start again.",
        }, indent=2))
        return 0
    # oauth mode: run device flow (requires google-auth-oauthlib)
    try:
        from services.soma_kajabi.gmail_oauth_device import run_device_flow_start
        result = run_device_flow_start(root)
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1
    except ImportError:
        print(json.dumps({
            "ok": False,
            "message": "Gmail OAuth requires google-auth-oauthlib. Use gmail.mode=imap with GMAIL_USER + GMAIL_APP_PASSWORD instead.",
        }))
        return 1


def _run_status() -> int:
    root = _repo_root()
    cfg, err = load_soma_kajabi_config(root)
    if err:
        print(json.dumps({"ok": False, "error_class": err}))
        return 1
    mode = cfg.get("gmail", {}).get("mode", "manual")
    path_str = cfg.get("gmail", {}).get("auth_secret_ref") or str(GMAIL_OAUTH_PATH)
    path = Path(path_str)
    present = path.exists() and path.stat().st_size > 0
    print(json.dumps({
        "ok": True,
        "mode": mode,
        "oauth_token_present": present if mode == "oauth" else None,
        "imap_ready": mode == "imap",  # Caller checks secrets separately
    }, indent=2))
    return 0


def _run_finalize() -> int:
    """Poll for OAuth completion and save token."""
    root = _repo_root()
    cfg, err = load_soma_kajabi_config(root)
    if err:
        print(json.dumps({"ok": False, "error_class": err}))
        return 1
    mode = cfg.get("gmail", {}).get("mode", "manual")
    if mode != "oauth":
        print(json.dumps({"ok": True, "message": f"Gmail mode is {mode}; OAuth finalize not needed"}))
        return 0
    try:
        from services.soma_kajabi.gmail_oauth_device import run_device_flow_finalize
        result = run_device_flow_finalize(root)
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1
    except ImportError:
        print(json.dumps({"ok": False, "message": "OAuth finalize requires google-auth-oauthlib"}))
        return 1


def main() -> int:
    if len(sys.argv) < 2:
        print(json.dumps({"ok": False, "message": "Usage: gmail_connect <start|status|finalize>"}))
        return 1
    action = sys.argv[1].lower()
    if action == "start":
        return _run_start()
    if action == "status":
        return _run_status()
    if action == "finalize":
        return _run_finalize()
    print(json.dumps({"ok": False, "message": f"Unknown action: {action}"}))
    return 1


if __name__ == "__main__":
    sys.exit(main())
