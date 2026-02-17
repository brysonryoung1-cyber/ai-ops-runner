#!/usr/bin/env python3
"""Kajabi connector bootstrap. Provides start/status/finalize for interactive session setup.

Prints instructions only; no secrets in output.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.soma_kajabi.connector_config import (
    KAJABI_STORAGE_STATE_PATH,
    load_soma_kajabi_config,
    _repo_root,
)


def _run_start() -> int:
    root = _repo_root()
    cfg, err = load_soma_kajabi_config(root)
    if err:
        print(json.dumps({"ok": False, "error_class": err, "message": "Config invalid"}))
        return 1
    path = cfg.get("kajabi", {}).get("storage_state_secret_ref") or str(KAJABI_STORAGE_STATE_PATH)
    print(json.dumps({
        "ok": True,
        "step": "bootstrap_start",
        "message": "To bootstrap Kajabi: 1) Log in at https://app.kajabi.com in a browser. 2) Use Playwright to save storage_state: context.storage_state(path='...'). 3) Copy the saved file to " + path,
        "storage_state_path": path,
    }, indent=2))
    return 0


def _run_status() -> int:
    root = _repo_root()
    cfg, err = load_soma_kajabi_config(root)
    if err:
        print(json.dumps({"ok": False, "error_class": err}))
        return 1
    path_str = cfg.get("kajabi", {}).get("storage_state_secret_ref") or str(KAJABI_STORAGE_STATE_PATH)
    path = Path(path_str)
    present = path.exists() and path.stat().st_size > 0
    print(json.dumps({
        "ok": True,
        "storage_state_present": present,
        "path": path_str,
    }, indent=2))
    return 0


def _run_finalize() -> int:
    import os
    root = _repo_root()
    cfg, err = load_soma_kajabi_config(root)
    if err:
        print(json.dumps({"ok": False, "error_class": err}))
        return 1
    path_str = cfg.get("kajabi", {}).get("storage_state_secret_ref") or str(KAJABI_STORAGE_STATE_PATH)
    path = Path(path_str)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Finalize expects the file to already exist (user copied it). Verify and enforce perms 0640.
    if path.exists():
        try:
            path.chmod(0o640)
        except OSError:
            pass
        # Prefer 1000:1000 if we have capability (e.g. on host); skip if not root
        try:
            os.chown(path, 1000, 1000)
        except (OSError, AttributeError):
            pass
        print(json.dumps({"ok": True, "message": "storage_state already present", "path": path_str}))
        return 0
    print(json.dumps({
        "ok": False,
        "message": f"storage_state not found at {path_str}. Copy the Playwright storage_state file there.",
    }))
    return 1


def main() -> int:
    if len(sys.argv) < 3:
        print(json.dumps({"ok": False, "message": "Usage: bootstrap <kajabi|gmail> <start|status|finalize>"}))
        return 1
    target = sys.argv[1].lower()
    action = sys.argv[2].lower()
    if target != "kajabi":
        print(json.dumps({"ok": False, "message": f"Unknown target: {target}"}))
        return 1
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
