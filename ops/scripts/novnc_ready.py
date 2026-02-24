"""Shared noVNC readiness logic for session_check, capture_interactive, auto_finish.

Before emitting WAITING_FOR_HUMAN: restart openclaw-novnc, poll novnc_probe up to 30s.
If probe fails: fail-closed with NOVNC_BACKEND_UNAVAILABLE and journal artifact path.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

NOVNC_PORT = 6080
VNC_PORT = 5900
PROBE_TIMEOUT = 30
JOURNAL_LINES = 200


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
    """Probe noVNC; if fails: restart openclaw-novnc, retry probe 3x. Return (ready, url, error_class, journal_artifact).

    Flow: run novnc_probe.sh → if fail: systemctl restart → retry probe 3x → if still fail: capture journal, fail-closed.
    If probe passes: (True, url, None, None).
    If probe fails after restart+retries: (False, url, "NOVNC_BACKEND_UNAVAILABLE", rel_path_to_journal).
    """
    url = _get_tailscale_url()
    env_dir = Path("/run/openclaw-novnc")
    try:
        env_dir.mkdir(parents=True, exist_ok=True)
        (env_dir / "next.env").write_text(
            f"OPENCLAW_NOVNC_RUN_ID={run_id}\n"
            f"OPENCLAW_NOVNC_ARTIFACT_DIR={artifact_dir}\n"
            f"OPENCLAW_NOVNC_PORT={NOVNC_PORT}\n"
            f"OPENCLAW_NOVNC_DISPLAY=:99\n"
            f"OPENCLAW_NOVNC_VNC_PORT={VNC_PORT}\n"
        )
    except OSError:
        return False, url, "NOVNC_BACKEND_UNAVAILABLE", None

    def _try_probe_with_restart() -> tuple[bool, str]:
        ok, reason = _run_probe()
        if ok:
            return True, ""
        try:
            subprocess.run(["systemctl", "restart", "openclaw-novnc"], capture_output=True, timeout=15)
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False, reason
        for attempt in range(3):
            time.sleep(2)
            ok, _ = _run_probe()
            if ok:
                return True, ""
        return False, reason

    ok, reason = _run_probe()
    if ok:
        return True, url, None, None

    ok, _ = _try_probe_with_restart()
    if ok:
        return True, url, None, None

    # Fail-closed: capture last 200 lines of journalctl into audit artifact
    journal_path = _capture_journal(artifact_dir)
    try:
        repo = Path(__file__).resolve().parents[2]
        rel = str(journal_path.relative_to(repo))
    except ValueError:
        rel = str(journal_path)
    return False, url, "NOVNC_BACKEND_UNAVAILABLE", rel
