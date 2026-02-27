#!/usr/bin/env python3
"""HumanGateWatcher — Auto-resume after login detected. No manual "Click Resume" required.

While a run is WAITING_FOR_HUMAN, polls every 60-120s: soma_kajabi_session_check.
When authenticated state detected (session_check PASS): calls soma_kajabi_reauth_and_resume
to export storage_state and run auto_finish. Stops cleanly if user cancels or new run supersedes.

Writes artifacts/soma_kajabi/human_gate/<run_id>/status.json each cycle.
When auto-resume triggers: artifacts/soma_kajabi/human_gate/<run_id>/AUTO_RESUME.md
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

POLL_INTERVAL_SEC = int(os.environ.get("SOMA_HUMAN_GATE_POLL_INTERVAL", "90"))
MAX_CYCLES = int(os.environ.get("SOMA_HUMAN_GATE_MAX_CYCLES", "20"))  # ~30 min at 90s


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


def _is_waiting_for_human(root: Path) -> tuple[bool, str | None]:
    """Check if Soma is in WAITING_FOR_HUMAN. Return (yes, run_id)."""
    # Check lock (auto_finish running) - if so, it's polling itself, we skip
    lock_path = root / "artifacts" / ".locks" / "soma_kajabi_auto_finish.json"
    if lock_path.exists():
        try:
            data = json.loads(lock_path.read_text())
            if data.get("active_run_id"):
                return True, data.get("active_run_id")
        except (OSError, json.JSONDecodeError):
            pass

    # Check last auto_finish RESULT.json
    af_root = root / "artifacts" / "soma_kajabi" / "auto_finish"
    if not af_root.exists():
        return False, None
    dirs = sorted([d for d in af_root.iterdir() if d.is_dir()], key=lambda d: d.name, reverse=True)
    for d in dirs[:5]:
        result_path = d / "RESULT.json"
        if result_path.exists():
            try:
                data = json.loads(result_path.read_text())
                if data.get("status") == "WAITING_FOR_HUMAN":
                    return True, d.name
            except (OSError, json.JSONDecodeError):
                pass
    return False, None


def _run_session_check(root: Path) -> tuple[bool, dict]:
    """Run soma_kajabi_session_check. Return (ok, parsed_result)."""
    venv = root / ".venv-hostd" / "bin" / "python"
    if not venv.exists():
        venv = Path(sys.executable)
    script = root / "ops" / "scripts" / "soma_kajabi_session_check.py"
    exit_node = root / "ops" / "with_exit_node.sh"
    exit_cfg = Path("/etc/ai-ops-runner/config/soma_kajabi_exit_node.txt")
    use_exit = exit_node.exists() and exit_cfg.exists() and exit_cfg.read_text().strip()
    cmd = [str(exit_node), "--", str(venv), str(script)] if use_exit else [str(venv), str(script)]
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=400,
            cwd=str(root),
        )
        lines = (r.stdout or "").strip().split("\n")
        last = lines[-1] if lines else "{}"
        if last.startswith("{") and last.endswith("}"):
            doc = json.loads(last)
            return bool(r.returncode == 0 and doc.get("ok")), doc
        return False, {}
    except (subprocess.TimeoutExpired, json.JSONDecodeError):
        return False, {}


def _run_reauth_and_resume(root: Path) -> int:
    """Run soma_kajabi_reauth_and_resume. Return exit code."""
    venv = root / ".venv-hostd" / "bin" / "python"
    if not venv.exists():
        venv = Path(sys.executable)
    script = root / "ops" / "scripts" / "soma_kajabi_reauth_and_resume.py"
    try:
        r = subprocess.run(
            [str(venv), str(script)],
            capture_output=True,
            timeout=2000,
            cwd=str(root),
        )
        return r.returncode
    except subprocess.TimeoutExpired:
        return -1


def _run_novnc_audit(root: Path, run_id: str) -> bool:
    """Run novnc_connectivity_audit. Return True if PASS."""
    script = root / "ops" / "scripts" / "novnc_connectivity_audit.py"
    if not script.exists():
        return True  # Skip if missing
    try:
        r = subprocess.run(
            [sys.executable, str(script), "--run-id", f"{run_id}_watcher", "--host", os.environ.get("OPENCLAW_TS_HOSTNAME", "aiops-1.tailc75c62.ts.net")],
            capture_output=True,
            timeout=60,
            cwd=str(root),
        )
        return r.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def main() -> int:
    root = _repo_root()
    waiting, run_id = _is_waiting_for_human(root)
    if not waiting or not run_id:
        return 0  # Nothing to watch

    # If auto_finish lock is held, it's polling itself — skip (avoid duplicate session_check)
    lock_path = root / "artifacts" / ".locks" / "soma_kajabi_auto_finish.json"
    if lock_path.exists():
        try:
            data = json.loads(lock_path.read_text())
            if data.get("active_run_id") and data.get("started_at"):
                return 0  # Auto_finish is running, it will auto-resume
        except (OSError, json.JSONDecodeError):
            pass

    gate_dir = root / "artifacts" / "soma_kajabi" / "human_gate" / run_id
    gate_dir.mkdir(parents=True, exist_ok=True)

    for cycle in range(MAX_CYCLES):
        # Re-check: maybe user cancelled or new run superseded
        waiting_now, run_id_now = _is_waiting_for_human(root)
        if not waiting_now or run_id_now != run_id:
            break

        novnc_ready = _run_novnc_audit(root, run_id)
        session_ok, sc_doc = _run_session_check(root)

        status = {
            "waiting_reason": "KAJABI_LOGIN_OR_2FA",
            "last_check_ts": datetime.now(timezone.utc).isoformat(),
            "novnc_ready": novnc_ready,
            "authenticated_detected": session_ok,
            "run_id": run_id,
            "cycle": cycle + 1,
        }
        (gate_dir / "status.json").write_text(json.dumps(status, indent=2))

        if session_ok:
            # Auto-resume: run reauth_and_resume (exports state + auto_finish)
            (gate_dir / "AUTO_RESUME.md").write_text(
                f"# Auto-Resume\n\n**Timestamp:** {datetime.now(timezone.utc).isoformat()}\n"
                f"**Evidence:** session_check PASS (Products shows both libraries)\n"
                f"**Action:** soma_kajabi_reauth_and_resume triggered\n"
            )
            rc = _run_reauth_and_resume(root)
            (gate_dir / "AUTO_RESUME.md").write_text(
                (gate_dir / "AUTO_RESUME.md").read_text() +
                f"\n**reauth_and_resume exit_code:** {rc}\n"
            )
            return 0 if rc == 0 else 1

        time.sleep(POLL_INTERVAL_SEC)

    return 0


if __name__ == "__main__":
    sys.exit(main())
