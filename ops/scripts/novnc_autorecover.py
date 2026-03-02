#!/usr/bin/env python3
"""noVNC Autorecover — single-loop state machine for automatic noVNC recovery.

Step sequence (one loop, no unbounded retries):
  1) doctor --fast
  2) shm_fix (if indicated by error_class/journal)
  3) restart
  4) routing_fix
  5) restart
  6) doctor DEEP
  7) doctor --fast (final verification)

On PASS at any doctor step: write autorecover_result.json (PASS) and exit 0.
On FAIL after exhausting all steps: emit fixpack via novnc_fixpack_emit.sh and exit 1.

Artifacts: artifacts/novnc_autorecover/<run_id>/
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

DOCTOR_FAST_TIMEOUT = 45
DOCTOR_DEEP_TIMEOUT = 120
SHM_FIX_TIMEOUT = 300
RESTART_TIMEOUT = 30
ROUTING_FIX_TIMEOUT = 180
RESTART_SETTLE_SEC = 12

NOVNC_PORT = 6080
VNC_PORT = 5900


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


def _run_cmd(
    cmd: list[str],
    timeout: int,
    cwd: str,
    env: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """Run a command, return (returncode, stdout, stderr). Never raises."""
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            env=env,
        )
        return r.returncode, r.stdout or "", r.stderr or ""
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"
    except OSError as e:
        return -1, "", str(e)


def _run_doctor(
    root: Path, run_id: str, fast: bool = False
) -> tuple[bool, str | None, str | None]:
    """Run openclaw_novnc_doctor. Return (ok, novnc_url, error_class)."""
    doctor = root / "ops" / "openclaw_novnc_doctor.sh"
    if not doctor.exists() or not os.access(doctor, os.X_OK):
        return False, None, "NOVNC_DOCTOR_MISSING"

    cmd = [str(doctor)]
    if fast:
        cmd.append("--fast")

    env = {
        **os.environ,
        "OPENCLAW_RUN_ID": run_id,
        "OPENCLAW_NOVNC_PORT": str(NOVNC_PORT),
        "OPENCLAW_NOVNC_VNC_PORT": str(VNC_PORT),
    }
    timeout = DOCTOR_FAST_TIMEOUT if fast else DOCTOR_DEEP_TIMEOUT
    rc, stdout, stderr = _run_cmd(cmd, timeout, str(root), env)

    if rc == -1:
        return False, None, "NOVNC_BACKEND_UNAVAILABLE"

    line = stdout.strip().split("\n")[-1] if stdout.strip() else ""
    if not line:
        return False, None, "NOVNC_DOCTOR_NO_OUTPUT"

    try:
        doc = json.loads(line)
    except json.JSONDecodeError:
        return False, None, "NOVNC_DOCTOR_NO_OUTPUT"

    url = doc.get("novnc_url") or None
    if rc == 0 and doc.get("ok"):
        return True, url, None
    return False, url, doc.get("error_class") or "NOVNC_BACKEND_UNAVAILABLE"


def _capture_journal(out_dir: Path) -> Path:
    """Capture noVNC journal to artifact dir."""
    path = out_dir / "journalctl.txt"
    try:
        r = subprocess.run(
            [
                "journalctl",
                "-u",
                "openclaw-novnc.service",
                "-n",
                "200",
                "--no-pager",
                "-o",
                "short-precise",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        path.write_text(r.stdout or "(empty)")
    except Exception:
        path.write_text("(journalctl unavailable)")
    return path


def _needs_shm_fix(journal_path: Path, error_class: str | None) -> bool:
    """Heuristic: does the failure indicate shm exhaustion?"""
    if error_class and "shm" in error_class.lower():
        return True
    try:
        text = journal_path.read_text()
        return "shmget" in text.lower() or "no space left" in text.lower()
    except Exception:
        return False


def main() -> int:
    root = _repo_root()
    run_id = os.environ.get(
        "OPENCLAW_RUN_ID",
        f"autorecover_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}",
    )
    out_dir = root / "artifacts" / "novnc_autorecover" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    steps: list[dict] = []

    def record(step_name: str, rc: int, detail: str = "") -> None:
        steps.append(
            {
                "step": step_name,
                "exit_code": rc,
                "detail": detail[:500],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

    # Step 1: doctor --fast
    ok, url, err_class = _run_doctor(root, f"{run_id}_fast1", fast=True)
    record("doctor_fast_1", 0 if ok else 1, err_class or "PASS")
    if ok:
        _write_result(out_dir, steps, True, url, run_id)
        return 0

    # Capture journal for diagnosis
    journal_path = _capture_journal(out_dir)

    # Step 2: shm_fix (conditional)
    if _needs_shm_fix(journal_path, err_class):
        shm_script = root / "ops" / "scripts" / "novnc_shm_fix.sh"
        if shm_script.exists():
            rc, stdout, stderr = _run_cmd(
                ["bash", str(shm_script)], SHM_FIX_TIMEOUT, str(root)
            )
            record("shm_fix", rc, stderr[:200] if rc != 0 else "ok")
        else:
            record("shm_fix", -1, "script_missing")
    else:
        record("shm_fix", 0, "skipped_not_indicated")

    # Step 3: restart
    rc, stdout, stderr = _run_cmd(
        ["systemctl", "restart", "openclaw-novnc"], RESTART_TIMEOUT, str(root)
    )
    record("restart_1", rc, stderr[:200] if rc != 0 else "ok")

    import time

    time.sleep(RESTART_SETTLE_SEC)

    # Step 4: routing_fix
    routing_script = root / "ops" / "scripts" / "openclaw_novnc_routing_fix.sh"
    if routing_script.exists():
        rc, stdout, stderr = _run_cmd(
            ["bash", str(routing_script)], ROUTING_FIX_TIMEOUT, str(root)
        )
        record("routing_fix", rc, stderr[:200] if rc != 0 else "ok")
    else:
        record("routing_fix", -1, "script_missing")

    # Step 5: restart (again after routing_fix)
    rc, stdout, stderr = _run_cmd(
        ["systemctl", "restart", "openclaw-novnc"], RESTART_TIMEOUT, str(root)
    )
    record("restart_2", rc, stderr[:200] if rc != 0 else "ok")
    time.sleep(RESTART_SETTLE_SEC)

    # Step 6: doctor DEEP
    ok, url, err_class = _run_doctor(root, f"{run_id}_deep", fast=False)
    record("doctor_deep", 0 if ok else 1, err_class or "PASS")
    if ok:
        _write_result(out_dir, steps, True, url, run_id)
        return 0

    # Step 7: doctor --fast (final check)
    ok, url, err_class = _run_doctor(root, f"{run_id}_fast2", fast=True)
    record("doctor_fast_2", 0 if ok else 1, err_class or "PASS")
    if ok:
        _write_result(out_dir, steps, True, url, run_id)
        return 0

    # All steps exhausted — emit fixpack
    _write_result(out_dir, steps, False, url, run_id)
    _emit_fixpack(root, out_dir, err_class or "NOVNC_NOT_READY", run_id)
    return 1


def _write_result(
    out_dir: Path,
    steps: list[dict],
    passed: bool,
    novnc_url: str | None,
    run_id: str,
) -> None:
    result = {
        "run_id": run_id,
        "status": "PASS" if passed else "FAIL",
        "novnc_url": novnc_url or "",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    (out_dir / "autorecover_result.json").write_text(json.dumps(result, indent=2))
    (out_dir / "steps_executed.json").write_text(json.dumps(steps, indent=2))


def _emit_fixpack(
    root: Path, out_dir: Path, error_class: str, run_id: str
) -> None:
    """Invoke novnc_fixpack_emit.sh to generate triage + evidence + CSR_PROMPT."""
    fixpack_script = root / "ops" / "scripts" / "novnc_fixpack_emit.sh"
    if not fixpack_script.exists():
        return

    # Build artifact pointer args
    pointer_args: list[str] = []
    for name in (
        "autorecover_result.json",
        "steps_executed.json",
        "journalctl.txt",
    ):
        p = out_dir / name
        if p.exists():
            pointer_args.append(f"{name.replace('.json','').replace('.txt','')}:{p}")

    # Include doctor artifact dirs if they exist
    doctor_base = root / "artifacts" / "novnc_debug"
    if doctor_base.exists():
        latest_dirs = sorted(
            [d for d in doctor_base.iterdir() if d.is_dir() and run_id in d.name],
            key=lambda d: d.name,
            reverse=True,
        )
        if latest_dirs:
            guard_result = latest_dirs[0] / "guard_result.json"
            if guard_result.exists():
                pointer_args.append(f"guard_result:{guard_result}")

    cmd = [
        "bash",
        str(fixpack_script),
        str(out_dir),
        error_class,
        "novnc_autorecover",
        "run novnc_autorecover or escalate to operator",
    ] + pointer_args

    try:
        subprocess.run(cmd, capture_output=True, timeout=30, cwd=str(root))
    except Exception:
        pass


if __name__ == "__main__":
    sys.exit(main())
