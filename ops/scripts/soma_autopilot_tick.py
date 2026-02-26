#!/usr/bin/env python3
"""Soma Autopilot Tick — Timer-driven trigger for soma_run_to_done.

Runs every 10 minutes via openclaw-soma-autopilot.timer.
- If flag missing: exit 0
- If active run exists (soma_run_to_done locked): exit 0 (ACTIVE)
- If last status WAITING_FOR_HUMAN: exit 0 (no spam)
- If backoff window active: exit 0 (BACKOFF)
- Deterministic recovery chain BEFORE Soma:
  1) hostd reachable — if not: attempt recover; if still down: BLOCKED
  2) openclaw_novnc_doctor (DEEP)
  3) If FAIL: shm_fix → restart → doctor retry
  4) If still FAIL: BLOCKED(novnc_not_ready), no Soma trigger
- Only if doctor PASS → trigger soma_run_to_done

Safety:
- Never spam restarts
- Backoff on infra failures (30 min cooldown after failure)
- BLOCKED after 3 consecutive infra failures (manual intervention required)

Artifacts: artifacts/soma_kajabi/autopilot/<timestamp>/status.json + status.md
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

CONFIG_FLAG = Path("/etc/ai-ops-runner/config/soma_autopilot_enabled.txt")
HQ_BASE = os.environ.get("OPENCLAW_HQ_BASE", "http://127.0.0.1:8787")
ADMIN_TOKEN = ""
for p in (
    "/etc/ai-ops-runner/secrets/openclaw_admin_token",
    "/etc/ai-ops-runner/secrets/openclaw_console_token",
    "/etc/ai-ops-runner/secrets/openclaw_api_token",
    "/etc/ai-ops-runner/secrets/openclaw_token",
):
    if Path(p).exists():
        ADMIN_TOKEN = Path(p).read_text().strip()
        break
MAX_INFRA_FAILURES = int(os.environ.get("OPENCLAW_SOMA_AUTOPILOT_MAX_INFRA_FAILURES", "3"))
BACKOFF_SEC = int(os.environ.get("OPENCLAW_SOMA_AUTOPILOT_BACKOFF_SEC", "1800"))


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


def _curl(method: str, path: str, data: dict | None = None, timeout: int = 15) -> tuple[int, str]:
    import urllib.request
    import urllib.error

    url = f"{HQ_BASE.rstrip('/')}{path}"
    headers = {"Content-Type": "application/json"}
    if ADMIN_TOKEN:
        headers["X-OpenClaw-Token"] = ADMIN_TOKEN
    req = urllib.request.Request(url, method=method, headers=headers)
    if data:
        req.data = json.dumps(data).encode("utf-8")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8") if e.fp else ""
    except Exception as e:
        return -1, str(e)


DOCTOR_FAST_TIMEOUT = 35
DOCTOR_DEEP_TIMEOUT = 90


def _journal_indicates_shm(root: Path) -> bool:
    """Check if openclaw-novnc journal indicates shmget or /dev/shm constraint."""
    try:
        r = subprocess.run(
            ["journalctl", "-u", "openclaw-novnc.service", "-n", "50", "--no-pager"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=str(root),
        )
        out = (r.stdout or "") + (r.stderr or "")
        return "shmget" in out or "No space left on device" in out or "/dev/shm" in out.lower()
    except Exception:
        return False


def _last_proof_status(root: Path) -> str | None:
    """Return last terminal status from run_to_done PROOF.json (SUCCESS, WAITING_FOR_HUMAN, FAILURE, etc)."""
    run_dir = root / "artifacts" / "soma_kajabi" / "run_to_done"
    if not run_dir.exists():
        return None
    dirs = sorted([d for d in run_dir.iterdir() if d.is_dir()], key=lambda d: d.name, reverse=True)
    for d in dirs[:5]:
        proof = d / "PROOF.json"
        if proof.exists():
            try:
                data = json.loads(proof.read_text())
                return data.get("status")
            except (json.JSONDecodeError, KeyError):
                continue
    return None


def _run_novnc_doctor(root: Path, fast: bool = False) -> tuple[bool, str | None]:
    """Run openclaw_novnc_doctor. Return (ok, error_class). FAST ~25s, DEEP ~90s."""
    doctor = root / "ops" / "openclaw_novnc_doctor.sh"
    if not doctor.exists() or not os.access(doctor, os.X_OK):
        return True, None
    args = [str(doctor)]
    if fast:
        args.append("--fast")
    timeout = DOCTOR_FAST_TIMEOUT if fast else DOCTOR_DEEP_TIMEOUT
    try:
        r = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(root),
        )
        line = (r.stdout or "").strip().split("\n")[-1]
        if line:
            doc = json.loads(line)
            if doc.get("ok", False):
                return True, None
            return False, doc.get("error_class")
        return False, None
    except (subprocess.TimeoutExpired, json.JSONDecodeError):
        return False, "DOCTOR_TIMEOUT"


def _is_soma_run_to_done_active() -> bool:
    """Check if soma_run_to_done is currently running (lock held)."""
    code, body = _curl("GET", "/api/exec?check=lock&action=soma_run_to_done", timeout=5)
    if code != 200:
        return False
    try:
        data = json.loads(body)
        return data.get("locked", False)
    except json.JSONDecodeError:
        return False


def _write_status_artifact(
    root: Path,
    outcome: str,
    run_id: str | None = None,
    current_status: str | None = None,
    error_class: str | None = None,
    fail_count: int = 0,
    blocked: bool = False,
) -> Path:
    """Write status to artifacts/soma_kajabi/autopilot/<timestamp>/status.json + status.md."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = root / "artifacts" / "soma_kajabi" / "autopilot" / ts
    out_dir.mkdir(parents=True, exist_ok=True)

    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "outcome": outcome,
        "run_id": run_id,
        "current_status": current_status,
        "error_class": error_class,
        "fail_count": fail_count,
        "blocked": blocked,
    }
    (out_dir / "status.json").write_text(json.dumps(payload, indent=2))
    md = f"# Soma Autopilot Status — {outcome}\n\n"
    md += f"- **Timestamp**: {payload['timestamp']}\n"
    md += f"- **Run ID**: {run_id or '—'}\n"
    md += f"- **Current status**: {current_status or '—'}\n"
    if error_class:
        md += f"- **Error**: {error_class}\n"
    if blocked:
        md += "\n**BLOCKED**: Repeated infra failures. Manual intervention required. See artifact links.\n"
    (out_dir / "status.md").write_text(md)
    return out_dir


def main() -> int:
    root = _repo_root()
    state_dir = Path(os.environ.get("OPENCLAW_SOMA_AUTOPILOT_STATE_DIR", "/var/lib/ai-ops-runner/soma_autopilot"))
    # Fallback to repo-local state when /var/lib not writable (e.g. Mac dev)
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
    except (PermissionError, FileNotFoundError):
        state_dir = root / "artifacts" / "soma_kajabi" / ".autopilot_state"
        state_dir.mkdir(parents=True, exist_ok=True)
    fail_file = state_dir / "infra_fail_count.txt"
    blocked_file = state_dir / "blocked"
    last_fail_ts_file = state_dir / "last_infra_fail_ts.txt"

    # 1. Check flag (write enabled state for API to read)
    if not CONFIG_FLAG.exists():
        (state_dir / "enabled.txt").write_text("0")
        _write_status_artifact(root, "SKIP", error_class="disabled")
        return 0
    (state_dir / "enabled.txt").write_text("1")

    # 2. Check BLOCKED
    if blocked_file.exists():
        fail_count = int(fail_file.read_text()) if fail_file.exists() else MAX_INFRA_FAILURES
        _write_status_artifact(
            root, "SKIP", current_status="BLOCKED", error_class="repeated_infra_failures",
            fail_count=fail_count, blocked=True
        )
        return 0

    # 3. Backoff check
    if fail_file.exists():
        fail_count = int(fail_file.read_text())
        if fail_count >= MAX_INFRA_FAILURES:
            blocked_file.touch()
            _write_status_artifact(
                root, "SKIP", current_status="BLOCKED", error_class="repeated_infra_failures",
                fail_count=fail_count, blocked=True
            )
            return 0
        if last_fail_ts_file.exists():
            try:
                last_ts = int(last_fail_ts_file.read_text())
                if time.time() - last_ts < BACKOFF_SEC:
                    _write_status_artifact(root, "SKIP", error_class="backoff", fail_count=fail_count)
                    return 0
            except (ValueError, OSError):
                pass

    # 4. Check active run (POST returns 409 if locked)
    if _is_soma_run_to_done_active():
        last_status = _last_proof_status(root)
        _write_status_artifact(root, "SKIP", current_status=last_status, error_class="active_run_exists")
        return 0

    # 5. Check last status WAITING_FOR_HUMAN
    last_status = _last_proof_status(root)
    if last_status == "WAITING_FOR_HUMAN":
        _write_status_artifact(root, "SKIP", current_status="WAITING_FOR_HUMAN")
        return 0

    # 6. Hostd reachable (attempt recover if not)
    code, _ = _curl("GET", "/api/exec?check=connectivity", timeout=10)
    if code != 200:
        try:
            subprocess.run(
                ["systemctl", "restart", "openclaw-hostd"],
                capture_output=True,
                timeout=10,
            )
            time.sleep(5)
            code, _ = _curl("GET", "/api/exec?check=connectivity", timeout=10)
        except Exception:
            pass
        if code != 200:
            fail_count = int(fail_file.read_text()) if fail_file.exists() else 0
            fail_count += 1
            fail_file.write_text(str(fail_count))
            last_fail_ts_file.write_text(str(int(time.time())))
            _write_status_artifact(root, "FAIL", error_class="HOSTD_UNREACHABLE", fail_count=fail_count)
            return 1

    # 7. noVNC doctor FAST first — only run shm_fix if journal indicates shm
    doctor_ok, doctor_err = _run_novnc_doctor(root, fast=True)
    if not doctor_ok:
        # Only run shm_fix when journal indicates shmget or /dev/shm constraint
        run_shm_fix = _journal_indicates_shm(root)
        if run_shm_fix:
            _curl("POST", "/api/exec", data={"action": "openclaw_novnc_shm_fix"}, timeout=300)
            time.sleep(5)
        # Restart only if service not active or ports missing (doctor_err hints)
        _curl("POST", "/api/exec", data={"action": "openclaw_novnc_restart"}, timeout=60)
        time.sleep(15)
        doctor_ok, _ = _run_novnc_doctor(root, fast=False)
        if not doctor_ok:
            _write_status_artifact(
                root, "SKIP", current_status="BLOCKED", error_class="novnc_not_ready"
            )
            return 0

    # 8. Trigger soma_run_to_done (doctor PASS)
    code, body = _curl("POST", "/api/exec", data={"action": "soma_run_to_done"}, timeout=10)
    if code == 409:
        _write_status_artifact(root, "SKIP", current_status=last_status, error_class="active_run_exists")
        return 0
    if code == 202:
        try:
            data = json.loads(body)
            run_id = data.get("run_id", "")
            _write_status_artifact(root, "TRIGGERED", run_id=run_id, current_status="running")
            if fail_file.exists():
                fail_file.write_text("0")
            if blocked_file.exists():
                blocked_file.unlink(missing_ok=True)
            return 0
        except json.JSONDecodeError:
            pass

    # Infra failure (502, 503, timeout, etc.)
    fail_count = int(fail_file.read_text()) if fail_file.exists() else 0
    fail_count += 1
    fail_file.write_text(str(fail_count))
    last_fail_ts_file.write_text(str(int(time.time())))
    err_class = "HOSTD_UNREACHABLE"
    try:
        d = json.loads(body)
        err_class = d.get("error_class", str(code))
    except (json.JSONDecodeError, TypeError):
        err_class = f"HTTP_{code}" if code > 0 else "CONNECT_FAILED"
    _write_status_artifact(
        root, "FAIL", error_class=err_class, fail_count=fail_count
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
