#!/usr/bin/env python3
"""Soma Kajabi unblock and run — discover → snapshot_debug → phase0 → finish_plan.

If discover returns KAJABI_CLOUDFLARE_BLOCKED, triggers soma_kajabi_capture_interactive
and waits for completion, then reruns the pipeline until PASS.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

KAJABI_CLOUDFLARE_BLOCKED = "KAJABI_CLOUDFLARE_BLOCKED"


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


def _run(cmd: list[str], timeout: int = 600) -> tuple[int, str]:
    """Run command, return (exit_code, stdout)."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(_repo_root()),
        )
        return result.returncode, result.stdout or ""
    except subprocess.TimeoutExpired:
        return -1, "timeout"
    except Exception as e:
        return -1, str(e)


def main() -> int:
    root = _repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    venv_python = root / ".venv-hostd" / "bin" / "python"
    if not venv_python.exists():
        venv_python = Path(sys.executable)

    max_capture_attempts = 1  # Only trigger capture once per run
    capture_attempts = 0

    while True:
        # 1. discover
        rc, out = _run([str(venv_python), str(root / "ops" / "scripts" / "kajabi_discover.py")], timeout=200)
        try:
            last_line = out.strip().split("\n")[-1] if out else "{}"
            doc = json.loads(last_line)
        except json.JSONDecodeError:
            doc = {}
        error_class = doc.get("error_class")

        if rc == 0 and doc.get("ok"):
            break
        if error_class == KAJABI_CLOUDFLARE_BLOCKED and capture_attempts < max_capture_attempts:
            capture_attempts += 1
            print(f"Cloudflare blocked. Running soma_kajabi_capture_interactive (attempt {capture_attempts})...", file=sys.stderr)
            cap_rc, cap_out = _run([str(venv_python), str(root / "ops" / "scripts" / "kajabi_capture_interactive.py")], timeout=1320)
            if cap_rc != 0:
                print(json.dumps({"ok": False, "error_class": "KAJABI_CAPTURE_INTERACTIVE_FAILED", "output": cap_out[:500]}))
                return 1
            continue
        if rc != 0:
            print(out, file=sys.stderr)
            print(json.dumps({"ok": False, "error_class": error_class or "KAJABI_DISCOVER_FAILED"}))
            return 1

    # 2. snapshot_debug
    rc, out = _run([str(venv_python), "-m", "services.soma_kajabi.snapshot_debug_runner"], timeout=200)
    if rc != 0:
        print(out, file=sys.stderr)
        print(json.dumps({"ok": False, "error_class": "KAJABI_SNAPSHOT_DEBUG_FAILED"}))
        return 1

    # 3. phase0
    rc, out = _run([str(venv_python), "-m", "services.soma_kajabi.phase0_runner"], timeout=320)
    if rc != 0:
        print(out, file=sys.stderr)
        print(json.dumps({"ok": False, "error_class": "KAJABI_PHASE0_FAILED"}))
        return 1

    # 4. finish_plan
    rc, out = _run([str(venv_python), "-m", "services.soma_kajabi.zane_finish_plan"], timeout=70)
    if rc != 0:
        print(out, file=sys.stderr)
        print(json.dumps({"ok": False, "error_class": "KAJABI_FINISH_PLAN_FAILED"}))
        return 1

    print(json.dumps({"ok": True, "message": "discover → snapshot_debug → phase0 → finish_plan PASS"}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
