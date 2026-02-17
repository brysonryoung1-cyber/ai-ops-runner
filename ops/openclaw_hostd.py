#!/usr/bin/env python3
"""
openclaw_hostd — Host-local allowlisted executor. No SSH.
Binds 127.0.0.1:8877 only. Auth via X-OpenClaw-Admin-Token from
/etc/ai-ops-runner/secrets/openclaw_admin_token. Fail-closed everywhere.
"""
from __future__ import annotations

import hmac
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse


def constant_time_compare(a: str, b: str) -> bool:
    """Compare two strings in constant time to avoid timing leaks."""
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))

VERSION = "1.0.0"
HOST = "127.0.0.1"
PORT = 8877
TOKEN_PATH = "/etc/ai-ops-runner/secrets/openclaw_admin_token"
ROOT_DIR = "/opt/ai-ops-runner"
ARTIFACTS_HOSTD = "artifacts/hostd"
MAX_STDOUT_BYTES = 2 * 1024 * 1024
MAX_STDERR_BYTES = 512 * 1024

ALLOWLIST = {
    "deploy_and_verify": {
        "cmd": ["bash", "-c", "cd /opt/ai-ops-runner && ./ops/deploy_pipeline.sh"],
        "timeout_sec": 900,
    },
    "doctor": {
        "cmd": ["bash", "-c", "cd /opt/ai-ops-runner && ./ops/openclaw_doctor.sh"],
        "timeout_sec": 120,
    },
    "apply": {
        "cmd": ["bash", "-c", "cd /opt/ai-ops-runner && ./ops/openclaw_apply_remote.sh"],
        "timeout_sec": 120,
    },
    "port_audit": {
        "cmd": ["bash", "-c", "cd /opt/ai-ops-runner && ./ops/show_port_audit.sh"],
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
        "cmd": ["bash", "-c", "cd /opt/ai-ops-runner && sudo ./ops/openclaw_install_guard.sh"],
        "timeout_sec": 30,
    },
    "llm_doctor": {
        "cmd": ["bash", "-c", "cd /opt/ai-ops-runner && python3 -m src.llm.doctor"],
        "timeout_sec": 30,
    },
    "soma_snapshot_home": {
        "cmd": [
            "bash",
            "-c",
            'cd /opt/ai-ops-runner && python3 -m services.soma_kajabi_sync.snapshot --product "Home User Library"',
        ],
        "timeout_sec": 120,
    },
    "soma_snapshot_practitioner": {
        "cmd": [
            "bash",
            "-c",
            'cd /opt/ai-ops-runner && python3 -m services.soma_kajabi_sync.snapshot --product "Practitioner Library"',
        ],
        "timeout_sec": 120,
    },
    "soma_harvest": {
        "cmd": ["bash", "-c", "cd /opt/ai-ops-runner && python3 -m services.soma_kajabi_sync.harvest"],
        "timeout_sec": 180,
    },
    "soma_mirror": {
        "cmd": [
            "bash",
            "-c",
            "cd /opt/ai-ops-runner && python3 -m services.soma_kajabi_sync.mirror --dry-run",
        ],
        "timeout_sec": 60,
    },
    "soma_status": {
        "cmd": [
            "bash",
            "-c",
            "cd /opt/ai-ops-runner && python3 -m services.soma_kajabi_sync.sms status",
        ],
        "timeout_sec": 15,
    },
    "soma_last_errors": {
        "cmd": [
            "bash",
            "-c",
            'cd /opt/ai-ops-runner && python3 -c "from services.soma_kajabi_sync.sms import get_last_errors; errs=get_last_errors(5); print(chr(10).join(f\\"{e[\'timestamp\'][:16]}: {e[\'message\']}\\" for e in errs) if errs else \'No recent errors.\')"',
        ],
        "timeout_sec": 10,
    },
    "sms_status": {
        "cmd": [
            "bash",
            "-c",
            "cd /opt/ai-ops-runner && python3 -m services.soma_kajabi_sync.sms test",
        ],
        "timeout_sec": 15,
    },
    "artifacts": {
        "cmd": [
            "bash",
            "-c",
            "ls -1dt /opt/ai-ops-runner/artifacts/* 2>/dev/null | head -n 15 && echo '---' && du -sh /opt/ai-ops-runner/artifacts/* 2>/dev/null | sort -h | tail -n 15",
        ],
        "timeout_sec": 10,
    },
}


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
    try:
        proc = subprocess.run(
            cmd,
            cwd=ROOT_DIR,
            capture_output=True,
            timeout=timeout_sec,
            env={**os.environ},
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
            self.send_json(200, {
                "ok": True,
                "version": VERSION,
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
    server = HTTPServer((HOST, PORT), Handler)
    sys.stderr.write(f"openclaw_hostd listening on {HOST}:{PORT}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
