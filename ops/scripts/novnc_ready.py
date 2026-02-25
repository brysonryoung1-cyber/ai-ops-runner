"""Shared noVNC readiness logic for session_check, capture_interactive, auto_finish.

Before emitting WAITING_FOR_HUMAN: run openclaw_novnc_doctor (framebuffer-aware).
Doctor PASS requires: service active + VNC port reachable + ws_stability_local/tailnet
verified + framebuffer.png exists. Doctor FAIL: do NOT fall through to probe (probe is
localhost-only; user needs tailnet). On doctor FAIL: restart openclaw-novnc, sleep 2,
retry up to 5 times. Only return novnc_url on PASS. Otherwise fail-closed with NOVNC_NOT_READY.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

NOVNC_PORT = 6080
VNC_PORT = 5900
ENSURE_MAX_RETRIES = 5
ENSURE_RESTART_SLEEP = 2


def novnc_display() -> str:
    """Canonical DISPLAY from /etc/ai-ops-runner/config/novnc_display.env."""
    cfg = Path("/etc/ai-ops-runner/config/novnc_display.env")
    if cfg.exists():
        for line in cfg.read_text().splitlines():
            line = line.strip()
            if line.startswith("DISPLAY=") and "=" in line:
                return line.split("=", 1)[1].strip().strip("'\"") or ":99"
    return ":99"
PROBE_TIMEOUT = 30
JOURNAL_LINES = 200


def _run_novnc_restart(root: Path) -> bool:
    """Run openclaw_novnc_restart (systemctl restart openclaw-novnc). Return True if restart succeeded."""
    try:
        r = subprocess.run(
            ["systemctl", "restart", "openclaw-novnc"],
            capture_output=True,
            timeout=15,
            cwd=str(root),
        )
        return r.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def _run_doctor(artifact_dir: Path, run_id: str) -> tuple[bool, str, str | None]:
    """Run openclaw_novnc_doctor. Return (ok, novnc_url, error_class)."""
    root = Path(__file__).resolve().parents[2]
    doctor = root / "ops" / "openclaw_novnc_doctor.sh"
    if not doctor.exists() or not os.access(doctor, os.X_OK):
        return False, "", "NOVNC_DOCTOR_MISSING"
    try:
        result = subprocess.run(
            [str(doctor)],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(root),
            env={
                **os.environ,
                "OPENCLAW_RUN_ID": run_id,
                "OPENCLAW_NOVNC_PORT": str(NOVNC_PORT),
                "OPENCLAW_NOVNC_VNC_PORT": str(VNC_PORT),
            },
        )
        line = (result.stdout or "").strip().split("\n")[-1]
        if not line:
            return False, "", "NOVNC_DOCTOR_NO_OUTPUT"
        doc = json.loads(line)
        url = doc.get("novnc_url", "") or ""
        if result.returncode == 0 and doc.get("ok"):
            return True, url, None
        err_class = doc.get("error_class") or "NOVNC_BACKEND_UNAVAILABLE"
        return False, url, err_class
    except (subprocess.TimeoutExpired, json.JSONDecodeError, KeyError):
        return False, "", "NOVNC_BACKEND_UNAVAILABLE"


def _get_tailscale_url() -> str:
    try:
        out = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if out.returncode == 0:
            data = json.loads(out.stdout)
            self_data = data.get("Self", {})
            dns_name = (self_data.get("DNSName") or "").rstrip(".")
            host_name = self_data.get("HostName") or ""
            if dns_name and ".ts.net" in dns_name:
                return f"http://{dns_name}:{NOVNC_PORT}/vnc.html?autoconnect=1"
            if host_name:
                return f"http://{host_name}:{NOVNC_PORT}/vnc.html?autoconnect=1"
    except Exception:
        pass
    try:
        out = subprocess.run(
            ["tailscale", "ip", "-4"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if out.returncode == 0 and out.stdout.strip():
            ip = out.stdout.strip().split()[0]
            return f"http://{ip}:{NOVNC_PORT}/vnc.html?autoconnect=1"
    except Exception:
        pass
    return f"http://<TAILSCALE_IP>:{NOVNC_PORT}/vnc.html?autoconnect=1"


def _run_probe() -> tuple[bool, str]:
    """Run novnc_probe.sh. Return (ok, reason)."""
    root = Path(__file__).resolve().parents[1]
    probe = root / "novnc_probe.sh"
    if not probe.exists():
        return False, "novnc_probe.sh missing"
    try:
        result = subprocess.run(
            [str(probe)],
            capture_output=True,
            text=True,
            timeout=10,
            env={**os.environ, "OPENCLAW_NOVNC_PORT": str(NOVNC_PORT), "OPENCLAW_NOVNC_VNC_PORT": str(VNC_PORT)},
        )
        if result.returncode == 0:
            return True, ""
        return False, (result.stdout or result.stderr or "probe failed").strip().split("\n")[-1][:80]
    except subprocess.TimeoutExpired:
        return False, "probe timeout"
    except Exception as e:
        return False, str(e)[:80]


def _capture_journal(artifact_dir: Path) -> Path:
    """Capture journal to artifact, return path."""
    path = artifact_dir / "openclaw_novnc_journal.txt"
    try:
        out = subprocess.run(
            ["journalctl", "-u", "openclaw-novnc.service", "-n", str(JOURNAL_LINES), "--no-pager", "-o", "short-precise"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if out.returncode == 0:
            path.write_text(out.stdout or "(empty)")
    except Exception:
        path.write_text("(journalctl unavailable)")
    return path


def ensure_novnc_ready(artifact_dir: Path, run_id: str) -> tuple[bool, str, str | None, str | None]:
    """Ensure noVNC ready; return (ready, url, error_class, journal_artifact).

    Hard contract: run openclaw_novnc_doctor; require service active + VNC port reachable +
    ws_stability_local/tailnet verified + framebuffer.png exists. On doctor FAIL: run
    openclaw_novnc_restart, sleep 2, retry. Up to 5 attempts with artifacts each time.
    Only return novnc_url on PASS. Otherwise fail-closed with NOVNC_NOT_READY.
    """
    root = Path(__file__).resolve().parents[2]
    last_url = ""
    last_err: str | None = None

    for attempt in range(1, ENSURE_MAX_RETRIES + 1):
        attempt_run_id = f"{run_id}_attempt{attempt}" if attempt > 1 else run_id
        doctor_ok, doctor_url, doctor_err = _run_doctor(artifact_dir, attempt_run_id)
        last_url = doctor_url or _get_tailscale_url()
        last_err = doctor_err

        if doctor_ok and doctor_url:
            return True, doctor_url, None, None

        if attempt < ENSURE_MAX_RETRIES:
            _run_novnc_restart(root)
            time.sleep(ENSURE_RESTART_SLEEP)

    # Exhausted retries: fail-closed with NOVNC_NOT_READY
    journal_path = _capture_journal(artifact_dir)
    try:
        rel = str(journal_path.relative_to(root))
    except ValueError:
        rel = str(journal_path)
    return False, last_url, "NOVNC_NOT_READY", rel


def ensure_novnc_ready_with_recovery(artifact_dir: Path, run_id: str) -> tuple[bool, str, str | None, str | None]:
    """Ensure noVNC ready with one mid-run recovery. On NOVNC_NOT_READY/NOVNC_BACKEND_UNAVAILABLE:
    restart openclaw-novnc, retry ensure_novnc_ready once. If retry still fails, return failure."""
    ready, url, err_class, journal = ensure_novnc_ready(artifact_dir, run_id)
    if ready:
        return True, url, None, None
    if err_class in ("NOVNC_BACKEND_UNAVAILABLE", "NOVNC_NOT_READY"):
        root = Path(__file__).resolve().parents[2]
        _run_novnc_restart(root)
        time.sleep(ENSURE_RESTART_SLEEP)
        return ensure_novnc_ready(artifact_dir, f"{run_id}_recovery")
    return False, url, err_class, journal
