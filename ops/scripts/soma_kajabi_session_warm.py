#!/usr/bin/env python3
"""Soma Kajabi Session Warm — optional timer to ping admin/products to keep session warm.

Only runs if /etc/ai-ops-runner/config/soma_kajabi_session_warm_enabled.txt exists.
Fail-closed: If EXIT_NODE_OFFLINE or exit-node enable fails, do not attempt Kajabi;
writes artifact SKIPPED_EXIT_NODE_OFFLINE.
Uses persistent Chromium profile, headless. No secrets.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

WARM_ENABLED_FILE = Path("/etc/ai-ops-runner/config/soma_kajabi_session_warm_enabled.txt")
EXIT_NODE_CONFIG = Path("/etc/ai-ops-runner/config/soma_kajabi_exit_node.txt")
KAJABI_ADMIN = "https://app.kajabi.com/admin"
KAJABI_SITES = "https://app.kajabi.com/admin/sites"
KAJABI_PRODUCTS_URL = "https://app.kajabi.com/admin/products"
KAJABI_CHROME_PROFILE_DIR = Path("/var/lib/openclaw/kajabi_chrome_profile")


def _repo_root() -> Path:
    env = os.environ.get("OPENCLAW_REPO_ROOT")
    if env and Path(env).exists():
        return Path(env)
    cwd = Path.cwd()
    for _ in range(10):
        if (cwd / "config" / "project_state.json").exists():
            return cwd
        if cwd == cwd.parent:
            break
        cwd = cwd.parent
    return Path(env or "/opt/ai-ops-runner")


def _run_with_exit_node(cmd: list[str], timeout: int) -> tuple[int, str]:
    root = _repo_root()
    if not EXIT_NODE_CONFIG.exists() or EXIT_NODE_CONFIG.read_text().strip() == "":
        try:
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
                cwd=str(root),
            )
            return result.returncode, (result.stdout or "") + (result.stderr or "")
        except subprocess.TimeoutExpired:
            return -1, "timeout"
        except Exception as e:
            return -1, str(e)

    wrapper = root / "ops" / "with_exit_node.sh"
    if not wrapper.exists():
        try:
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout, cwd=str(root))
            return result.returncode, (result.stdout or "") + (result.stderr or "")
        except Exception as e:
            return -1, str(e)

    full_cmd = [str(wrapper), "--", *cmd]
    try:
        result = subprocess.run(
            full_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            cwd=str(root),
        )
        out = (result.stdout or "") + (result.stderr or "")
        if result.returncode != 0 and ("EXIT_NODE_OFFLINE" in out or "EXIT_NODE_ENABLE_FAILED" in out):
            return result.returncode, out
        return result.returncode, out
    except subprocess.TimeoutExpired:
        return -1, "timeout"
    except Exception as e:
        return -1, str(e)


def _do_warm() -> int:
    """Headless ping of Kajabi admin/products using persistent profile."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return 0

    profile_dir = str(KAJABI_CHROME_PROFILE_DIR)
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[f"--user-data-dir={profile_dir}"],
        )
        try:
            context = browser.new_context()
            page = context.new_page()
            page.goto(KAJABI_ADMIN, wait_until="domcontentloaded", timeout=30000)
            page.goto(KAJABI_SITES, wait_until="domcontentloaded", timeout=30000)
            page.goto(KAJABI_PRODUCTS_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
        finally:
            browser.close()
    return 0


def main() -> int:
    # Inner mode: run only _do_warm (invoked by with_exit_node)
    if os.environ.get("SOMA_KAJABI_WARM_INNER") == "1":
        _do_warm()
        return 0

    if not WARM_ENABLED_FILE.exists() or WARM_ENABLED_FILE.read_text().strip() == "":
        return 0

    root = _repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    run_id = f"session_warm_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"
    out_dir = root / "artifacts" / "soma_kajabi" / "session_warm" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    venv_python = root / ".venv-hostd" / "bin" / "python"
    if not venv_python.exists():
        venv_python = Path(sys.executable)

    warm_script = root / "ops" / "scripts" / "soma_kajabi_session_warm.py"
    inner_cmd = ["env", "SOMA_KAJABI_WARM_INNER=1", "ARTIFACT_DIR=" + str(out_dir), str(venv_python), str(warm_script)]

    rc, out = _run_with_exit_node(inner_cmd, timeout=120)
    if rc != 0 and ("EXIT_NODE_OFFLINE" in out or "EXIT_NODE_ENABLE_FAILED" in out):
        (out_dir / "SKIPPED_EXIT_NODE_OFFLINE").write_text(
            json.dumps({
                "ok": False,
                "error_class": "EXIT_NODE_OFFLINE",
                "message": "Exit node offline. Session warm skipped.",
                "run_id": run_id,
            }, indent=2)
        )
        return 0

    try:
        (out_dir / "summary.json").write_text(json.dumps({
            "run_id": run_id,
            "ok": True,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        }, indent=2))
    except Exception:
        pass

    return 0


if __name__ == "__main__":
    # Inner mode: _do_warm writes to ARTIFACT_DIR if set
    if os.environ.get("SOMA_KAJABI_WARM_INNER") == "1":
        _do_warm()
        sys.exit(0)
    sys.exit(main())
