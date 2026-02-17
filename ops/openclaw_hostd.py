#!/usr/bin/env python3
"""
openclaw_hostd — Host-local allowlisted executor. No SSH.
Binds 127.0.0.1:8877 only. Auth via X-OpenClaw-Admin-Token from
/etc/ai-ops-runner/secrets/openclaw_admin_token. Fail-closed everywhere.
Console reaches hostd via host network (network_mode: host).
"""
from __future__ import annotations

import hmac
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse


def get_version() -> str:
    """Short git SHA if in repo, else VERSION."""
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT_DIR,
            capture_output=True,
            text=True,
            timeout=2,
        )
        if r.returncode == 0 and r.stdout and len(r.stdout.strip()) >= 7:
            return r.stdout.strip()
    except Exception:
        pass
    return VERSION


def constant_time_compare(a: str, b: str) -> bool:
    """Compare two strings in constant time to avoid timing leaks."""
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))

VERSION = "1.0.0"
HOST = "127.0.0.1"
PORT = 8877
TOKEN_PATH = "/etc/ai-ops-runner/secrets/openclaw_admin_token"
ROOT_DIR = os.environ.get("OPENCLAW_REPO_ROOT", "/opt/ai-ops-runner")
ARTIFACTS_HOSTD = "artifacts/hostd"
MAX_STDOUT_BYTES = 2 * 1024 * 1024
MAX_STDERR_BYTES = 512 * 1024


def _allowlist() -> dict:
    """Build allowlist with ROOT_DIR so CI can set OPENCLAW_REPO_ROOT."""
    return {
        "deploy_and_verify": {
            "cmd": ["bash", "-c", f"cd {ROOT_DIR} && ./ops/deploy_pipeline.sh"],
            "timeout_sec": 900,
        },
        "doctor": {
            "cmd": ["bash", "-c", f"cd {ROOT_DIR} && ./ops/openclaw_doctor.sh"],
            "timeout_sec": 180,
        },
        "apply": {
            "cmd": ["bash", "-c", f"cd {ROOT_DIR} && ./ops/openclaw_apply_remote.sh"],
            "timeout_sec": 120,
        },
        "port_audit": {
            "cmd": ["bash", "-c", f"cd {ROOT_DIR} && ./ops/show_port_audit.sh"],
            "timeout_sec": 60,
        },
        "tail_guard_log": {
            "cmd": [
                "bash",
                "-c",
                "journalctl -u openclaw-guard.service -n 200 --no-pager",
            ],
            "timeout_sec": 30,
        },
        "timer": {
            "cmd": ["systemctl", "status", "openclaw-guard.timer", "--no-pager"],
            "timeout_sec": 10,
        },
        "guard": {
            "cmd": ["bash", "-c", f"cd {ROOT_DIR} && sudo ./ops/openclaw_install_guard.sh"],
            "timeout_sec": 30,
        },
        "llm_doctor": {
            "cmd": ["bash", "-c", f"cd {ROOT_DIR} && python3 -m src.llm.doctor"],
            "timeout_sec": 30,
        },
        "soma_snapshot_home": {
            "cmd": [
                "bash",
                "-c",
                f'cd {ROOT_DIR} && python3 -m services.soma_kajabi_sync.snapshot --product "Home User Library"',
            ],
            "timeout_sec": 120,
        },
        "soma_snapshot_practitioner": {
            "cmd": [
                "bash",
                "-c",
                f'cd {ROOT_DIR} && python3 -m services.soma_kajabi_sync.snapshot --product "Practitioner Library"',
            ],
            "timeout_sec": 120,
        },
        "soma_harvest": {
            "cmd": ["bash", "-c", f"cd {ROOT_DIR} && python3 -m services.soma_kajabi_sync.harvest"],
            "timeout_sec": 180,
        },
        "soma_mirror": {
            "cmd": [
                "bash",
                "-c",
                f"cd {ROOT_DIR} && python3 -m services.soma_kajabi_sync.mirror --dry-run",
            ],
            "timeout_sec": 60,
        },
        "soma_status": {
            "cmd": [
                "bash",
                "-c",
                f"cd {ROOT_DIR} && python3 -m services.soma_kajabi_sync.sms status",
            ],
            "timeout_sec": 15,
        },
        "soma_kajabi_phase0": {
            "cmd": [
                "bash",
                "-c",
                f"cd {ROOT_DIR} && python3 -m services.soma_kajabi.phase0_runner",
            ],
            "timeout_sec": 300,
        },
        "soma_connectors_status": {
            "cmd": [
                "bash",
                "-c",
                f"cd {ROOT_DIR} && python3 -m services.soma_kajabi.connectors_status",
            ],
            "timeout_sec": 15,
        },
        "soma_kajabi_bootstrap_start": {
            "cmd": [
                "bash",
                "-c",
                f"cd {ROOT_DIR} && python3 -m services.soma_kajabi.bootstrap kajabi start",
            ],
            "timeout_sec": 30,
        },
        "soma_kajabi_bootstrap_status": {
            "cmd": [
                "bash",
                "-c",
                f"cd {ROOT_DIR} && python3 -m services.soma_kajabi.bootstrap kajabi status",
            ],
            "timeout_sec": 10,
        },
        "soma_kajabi_bootstrap_finalize": {
            "cmd": [
                "bash",
                "-c",
                f"cd {ROOT_DIR} && python3 -m services.soma_kajabi.bootstrap kajabi finalize",
            ],
            "timeout_sec": 30,
        },
        "soma_kajabi_gmail_connect_start": {
            "cmd": [
                "bash",
                "-c",
                f"cd {ROOT_DIR} && python3 -m services.soma_kajabi.gmail_connect start",
            ],
            "timeout_sec": 30,
        },
        "soma_kajabi_gmail_connect_status": {
            "cmd": [
                "bash",
                "-c",
                f"cd {ROOT_DIR} && python3 -m services.soma_kajabi.gmail_connect status",
            ],
            "timeout_sec": 10,
        },
        "soma_kajabi_gmail_connect_finalize": {
            "cmd": [
                "bash",
                "-c",
                f"cd {ROOT_DIR} && python3 -m services.soma_kajabi.gmail_connect finalize",
            ],
            "timeout_sec": 60,
        },
        "soma_last_errors": {
            "cmd": [
                "bash",
                "-c",
                f'cd {ROOT_DIR} && python3 -c "from services.soma_kajabi_sync.sms import get_last_errors; errs=get_last_errors(5); print(chr(10).join(f\\"{{e[\'timestamp\'][:16]}}: {{e[\'message\']}}\\" for e in errs) if errs else \'No recent errors.\')"',
            ],
            "timeout_sec": 10,
        },
        "sms_status": {
            "cmd": [
                "bash",
                "-c",
                f"cd {ROOT_DIR} && python3 -m services.soma_kajabi_sync.sms test",
            ],
            "timeout_sec": 15,
        },
        "artifacts": {
            "cmd": [
                "bash",
                "-c",
                f"ls -1dt {ROOT_DIR}/artifacts/* 2>/dev/null | head -n 15 && echo '---' && du -sh {ROOT_DIR}/artifacts/* 2>/dev/null | sort -h | tail -n 15",
            ],
            "timeout_sec": 10,
        },
        "orb.backtest.bulk": {
            "cmd": ["bash", "-c", f"cd {ROOT_DIR} && ./ops/scripts/orb_backtest_bulk.sh"],
            "timeout_sec": 600,
        },
        "orb.backtest.confirm_nt8": {
            "cmd": ["bash", "-c", f"cd {ROOT_DIR} && ./ops/scripts/orb_backtest_confirm_nt8.sh"],
            "timeout_sec": 120,
        },
        "pred_markets.mirror.run": {
            "cmd": ["bash", "-c", f"cd {ROOT_DIR} && python3 -m services.pred_markets.run mirror_run"],
            "timeout_sec": 300,
        },
        "pred_markets.mirror.backfill": {
            "cmd": ["bash", "-c", f"cd {ROOT_DIR} && python3 -m services.pred_markets.run mirror_backfill"],
            "timeout_sec": 600,
        },
        "pred_markets.report.health": {
            "cmd": ["bash", "-c", f"cd {ROOT_DIR} && python3 -m services.pred_markets.run report_health"],
            "timeout_sec": 60,
        },
        "pred_markets.report.daily": {
            "cmd": ["bash", "-c", f"cd {ROOT_DIR} && python3 -m services.pred_markets.run report_daily"],
            "timeout_sec": 60,
        },
    }


ALLOWLIST = _allowlist()

ORB_BACKTEST_ACTIONS = {"orb.backtest.bulk", "orb.backtest.confirm_nt8"}
REQUIRED_CONDITION = "Set gates.allow_orb_backtests=true after Soma Phase0 baseline PASS"


def load_project_state() -> dict:
    """Read config/project_state.json. Return {} on missing or error."""
    path = os.path.join(ROOT_DIR, "config", "project_state.json")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def is_orb_backtest_allowed() -> tuple[bool, str]:
    """Check gates.allow_orb_backtests and Soma Phase0 baseline. Return (allowed, reason)."""
    state = load_project_state()
    gates = state.get("gates") or {}
    if gates.get("allow_orb_backtests") is not True:
        return False, "gates.allow_orb_backtests is not true"
    projects = state.get("projects") or {}
    sk = projects.get("soma_kajabi") or {}
    if sk.get("phase0_baseline_status") != "PASS":
        return False, "projects.soma_kajabi.phase0_baseline_status is not PASS"
    return True, ""


def write_blocked_artifact(run_id: str, action: str) -> None:
    """Write artifacts/backtests/blocked/<run_id>/SUMMARY.md and blocked.json."""
    blocked_dir = os.path.join(ROOT_DIR, "artifacts", "backtests", "blocked", run_id)
    os.makedirs(blocked_dir, exist_ok=True)
    summary = (
        f"# ORB Backtest Blocked (Soma-First Gate)\n\n"
        f"- **Run ID**: {run_id}\n"
        f"- **Action**: {action}\n"
        f"- **Required condition**: {REQUIRED_CONDITION}\n"
        f"- **Unlock**: Set `gates.allow_orb_backtests=true` in config/project_state.json after Soma Phase 0 baseline PASS.\n"
    )
    summary_path = os.path.join(blocked_dir, "SUMMARY.md")
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write(summary)
    blocked_json = {
        "run_id": run_id,
        "action": action,
        "error_class": "LANE_LOCKED_SOMA_FIRST",
        "required_condition": REQUIRED_CONDITION,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }
    with open(os.path.join(blocked_dir, "blocked.json"), "w", encoding="utf-8") as f:
        json.dump(blocked_json, f, indent=2)


def load_admin_token() -> str | None:
    """Read admin token from file. Returns None if file missing or unreadable."""
    try:
        if not os.path.isfile(TOKEN_PATH):
            return None
        with open(TOKEN_PATH, "r", encoding="utf-8") as f:
            t = f.read().strip()
            return t if t else None
    except OSError:
        return None


def run_action(action: str, run_id: str) -> tuple[int, str, str, bool]:
    """Run allowlisted action. Returns (exit_code, stdout, stderr, truncated)."""
    if action not in ALLOWLIST:
        return (-1, "", f"Action not in allowlist: {action}", False)
    spec = ALLOWLIST[action]
    cmd = spec["cmd"]
    timeout_sec = spec["timeout_sec"]
    started_at = datetime.now(timezone.utc).isoformat()
    art_dir = os.path.join(ROOT_DIR, ARTIFACTS_HOSTD, run_id)
    os.makedirs(art_dir, exist_ok=True)
    stdout_path = os.path.join(art_dir, "stdout.txt")
    stderr_path = os.path.join(art_dir, "stderr.txt")
    result_path = os.path.join(art_dir, "hostd_result.json")
    env = {**os.environ, "OPENCLAW_RUN_ID": run_id}
    try:
        proc = subprocess.run(
            cmd,
            cwd=ROOT_DIR,
            capture_output=True,
            timeout=timeout_sec,
            env=env,
        )
        out_b = proc.stdout or b""
        err_b = proc.stderr or b""
        truncated = False
        if len(out_b) > MAX_STDOUT_BYTES:
            out_b = out_b[:MAX_STDOUT_BYTES] + b"\n... [truncated]"
            truncated = True
        if len(err_b) > MAX_STDERR_BYTES:
            err_b = err_b[:MAX_STDERR_BYTES] + b"\n... [truncated]"
            truncated = True
        stdout_s = out_b.decode("utf-8", errors="replace")
        stderr_s = err_b.decode("utf-8", errors="replace")
        finished_at = datetime.now(timezone.utc).isoformat()
        with open(stdout_path, "w", encoding="utf-8") as f:
            f.write(stdout_s)
        with open(stderr_path, "w", encoding="utf-8") as f:
            f.write(stderr_s)
        result = {
            "ok": proc.returncode == 0,
            "action": action,
            "exit_code": proc.returncode,
            "started_at": started_at,
            "finished_at": finished_at,
            "artifact_dir": f"{ARTIFACTS_HOSTD}/{run_id}",
            "truncated": truncated,
        }
        with open(result_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)
        return (proc.returncode, stdout_s, stderr_s, truncated)
    except subprocess.TimeoutExpired:
        finished_at = datetime.now(timezone.utc).isoformat()
        err_s = f"Action timed out after {timeout_sec}s"
        with open(stderr_path, "w", encoding="utf-8") as f:
            f.write(err_s)
        result = {
            "ok": False,
            "action": action,
            "exit_code": None,
            "started_at": started_at,
            "finished_at": finished_at,
            "artifact_dir": f"{ARTIFACTS_HOSTD}/{run_id}",
            "truncated": True,
        }
        with open(result_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)
        return (-1, "", err_s, True)
    except Exception as e:
        finished_at = datetime.now(timezone.utc).isoformat()
        err_s = str(e)
        try:
            with open(stderr_path, "w", encoding="utf-8") as f:
                f.write(err_s)
            result = {
                "ok": False,
                "action": action,
                "exit_code": None,
                "started_at": started_at,
                "finished_at": finished_at,
                "artifact_dir": f"{ARTIFACTS_HOSTD}/{run_id}",
                "truncated": False,
            }
            with open(result_path, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2)
        except OSError:
            pass
        return (-1, "", err_s, False)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # No secrets in logs; log method and path only
        sys.stderr.write(f"[hostd] {self.command} {args[0]}\n")

    def send_json(self, status: int, body: dict) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode("utf-8"))

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            start_time = getattr(Handler, "_start_time", None)
            uptime_seconds = int(time.monotonic() - start_time) if start_time is not None else 0
            version = getattr(Handler, "_version", None) or get_version()
            self.send_json(200, {
                "ok": True,
                "version": version,
                "uptime_seconds": uptime_seconds,
                "time": datetime.now(timezone.utc).isoformat(),
            })
            return
        self.send_json(404, {"error": "Not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/exec":
            self.send_json(404, {"error": "Not found"})
            return
        token = self.headers.get("X-OpenClaw-Admin-Token") or ""
        expected = load_admin_token()
        if expected is None:
            self.send_json(503, {"error": "admin not configured"})
            return
        if not token or not constant_time_compare(token, expected):
            self.send_json(403, {"error": "Forbidden"})
            return
        content_length = self.headers.get("Content-Length")
        if not content_length or int(content_length) > 4096:
            self.send_json(400, {"error": "Invalid or missing body"})
            return
        body = self.rfile.read(int(content_length)).decode("utf-8", errors="replace")
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self.send_json(400, {"error": "Invalid JSON"})
            return
        action = data.get("action") if isinstance(data, dict) else None
        if not action or not isinstance(action, str):
            self.send_json(400, {"error": "Missing or invalid action"})
            return
        if action not in ALLOWLIST:
            self.send_json(403, {"error": "Action not allowlisted"})
            return
        run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + os.urandom(4).hex()
        # Soma-first gate: block orb.backtest.* until baseline PASS and gate unlocked
        if action in ORB_BACKTEST_ACTIONS:
            allowed, reason = is_orb_backtest_allowed()
            if not allowed:
                write_blocked_artifact(run_id, action)
                self.send_response(423)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "ok": False,
                    "action": action,
                    "error_class": "LANE_LOCKED_SOMA_FIRST",
                    "required_condition": REQUIRED_CONDITION,
                    "run_id": run_id,
                    "artifact_dir": f"artifacts/backtests/blocked/{run_id}",
                }).encode("utf-8"))
                return
        exit_code, stdout, stderr, truncated = run_action(action, run_id)
        self.send_json(200, {
            "ok": exit_code == 0,
            "action": action,
            "stdout": stdout,
            "stderr": stderr,
            "exitCode": exit_code,
            "artifact_dir": f"{ARTIFACTS_HOSTD}/{run_id}",
            "truncated": truncated,
        })


def main() -> int:
    if not os.path.isdir(ROOT_DIR):
        sys.stderr.write(f"ROOT_DIR {ROOT_DIR} not found; run on VPS.\n")
        return 1
    start_time = time.monotonic()
    server = HTTPServer((HOST, PORT), Handler)
    Handler._start_time = start_time  # type: ignore[attr-defined]
    Handler._version = get_version()  # type: ignore[attr-defined]
    sys.stderr.write(f"openclaw_hostd listening on {HOST}:{PORT}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
