#!/usr/bin/env python3
"""Soma Fix and Retry — deterministic recovery chain + soma_run_to_done.

One-click recovery: hostd recover (if needed) → shm_fix → restart → doctor (DEEP) → soma_run_to_done.
Lock-aware: no duplicates. Output includes new run_id, artifact_dir, terminal status or WAITING_FOR_HUMAN.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

# Shared trigger client — single source of truth for exec POST + status handling
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ops.lib.exec_trigger import hq_request, trigger_exec  # noqa: E402

HQ_BASE = os.environ.get("OPENCLAW_HQ_BASE", "http://127.0.0.1:8787")

DOCTOR_TIMEOUT = 90


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


def _run_doctor(root: Path) -> bool:
    doctor = root / "ops" / "openclaw_novnc_doctor.sh"
    if not doctor.exists() or not os.access(doctor, os.X_OK):
        return True
    try:
        r = subprocess.run(
            [str(doctor)],
            capture_output=True,
            text=True,
            timeout=DOCTOR_TIMEOUT,
            cwd=str(root),
        )
        if r.returncode != 0:
            return False
        line = (r.stdout or "").strip().split("\n")[-1]
        if line:
            doc = json.loads(line)
            return doc.get("ok", False)
        return False
    except (subprocess.TimeoutExpired, json.JSONDecodeError):
        return False


def _trigger_soma_run_to_done() -> int:
    """Trigger soma_run_to_done via the shared client. Returns exit code."""
    tr = trigger_exec("soma_kajabi", "soma_run_to_done")
    if tr.state == "ACCEPTED":
        run_id = tr.run_id or ""
        print(json.dumps({
            "ok": True,
            "run_id": run_id,
            "status": "triggered",
            "message": "soma_run_to_done triggered. Poll /api/runs?id=" + run_id,
        }))
        return 0
    if tr.state == "ALREADY_RUNNING":
        print(json.dumps({
            "ok": False,
            "error_class": "ALREADY_RUNNING",
            "message": f"Run already in progress for project=soma_kajabi. Not starting a second run.",
            "active_run_id": tr.run_id or "(unknown)",
        }))
        return 0
    print(json.dumps({
        "ok": False,
        "error_class": "TRIGGER_FAILED",
        "message": tr.message,
        "project": "soma_kajabi",
        "action": "soma_run_to_done",
    }))
    return 1


def main() -> int:
    root = _repo_root()

    # 1. Check lock — if soma_run_to_done active, refuse
    code, body = hq_request("GET", "/api/exec?check=lock&action=soma_run_to_done", timeout=5)
    if code == 200:
        try:
            data = json.loads(body)
            if data.get("locked", False):
                print(json.dumps({
                    "ok": False,
                    "error_class": "ALREADY_RUNNING",
                    "message": "soma_run_to_done is already running. Wait for completion.",
                }))
                return 1
        except json.JSONDecodeError:
            pass

    # 2. Hostd reachable
    code, _ = hq_request("GET", "/api/exec?check=connectivity", timeout=10)
    if code != 200:
        print(json.dumps({
            "ok": False,
            "error_class": "HOSTD_UNREACHABLE",
            "message": "Host Executor unreachable. Restart openclaw-hostd.",
        }))
        return 1

    # 3. Doctor (DEEP)
    if _run_doctor(root):
        return _trigger_soma_run_to_done()

    # 4. Recovery chain: shm_fix → restart → doctor
    tr = trigger_exec("system", "openclaw_novnc_shm_fix", timeout=200)
    if tr.state == "FAILED":
        print(json.dumps({
            "ok": False,
            "error_class": "SHM_FIX_FAILED",
            "message": "openclaw_novnc_shm_fix failed.",
        }))
        return 1

    time.sleep(5)
    tr = trigger_exec("system", "openclaw_novnc_restart", timeout=30)
    if tr.state == "FAILED":
        print(json.dumps({
            "ok": False,
            "error_class": "NOVNC_RESTART_FAILED",
            "message": "openclaw_novnc_restart failed.",
        }))
        return 1

    time.sleep(15)
    if not _run_doctor(root):
        print(json.dumps({
            "ok": False,
            "error_class": "NOVNC_NOT_READY",
            "message": "noVNC doctor still FAIL after shm_fix + restart. Manual intervention required.",
        }))
        return 1

    # 5. Doctor PASS — trigger soma_run_to_done
    return _trigger_soma_run_to_done()


if __name__ == "__main__":
    sys.exit(main())
