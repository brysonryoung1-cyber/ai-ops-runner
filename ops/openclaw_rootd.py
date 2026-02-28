#!/usr/bin/env python3
"""
openclaw_rootd — Root-only privileged helper daemon for OpenClaw.

Listens on a Unix socket: /run/openclaw/rootd.sock
Executes ONLY allowlisted commands with strict validation.
Requires HMAC signature on every request using key from
/etc/ai-ops-runner/secrets/rootd_hmac_key (root-readable only).

Every execution writes audit records to:
  artifacts/system/rootd_audit/<run_id>/

Never logs request bodies containing sensitive fields.
Fail-closed: if policy denies, returns single clear reason.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer


SOCKET_PATH = "/run/openclaw/rootd.sock"
HMAC_KEY_PATH = "/etc/ai-ops-runner/secrets/rootd_hmac_key"
ROOT_DIR = os.environ.get("OPENCLAW_REPO_ROOT", "/opt/ai-ops-runner")
AUDIT_BASE = os.path.join(ROOT_DIR, "artifacts", "system", "rootd_audit")
MAX_REQUEST_SIZE = 32768
VERSION = "1.0.0"

SENSITIVE_FIELDS = frozenset({"content", "secret", "key", "token", "password", "hmac"})

sys.path.insert(0, ROOT_DIR)
try:
    from ops.policy.policy_evaluator import PolicyEvaluator
    _evaluator = PolicyEvaluator(os.path.join(ROOT_DIR, "ops", "policy", "permissions.json"))
except Exception as e:
    sys.stderr.write(f"[rootd] FATAL: cannot load policy evaluator: {e}\n")
    sys.exit(1)


def _load_hmac_key() -> bytes | None:
    try:
        with open(HMAC_KEY_PATH, "rb") as f:
            key = f.read().strip()
            return key if len(key) >= 16 else None
    except OSError:
        return None


def _verify_hmac(payload: bytes, signature: str) -> bool:
    key = _load_hmac_key()
    if not key:
        return False
    expected = hmac.new(key, payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _compute_hmac(payload: bytes) -> str:
    key = _load_hmac_key()
    if not key:
        return ""
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def _redact_body_for_log(data: dict) -> dict:
    """Remove sensitive fields from data before logging."""
    return {k: ("***REDACTED***" if k in SENSITIVE_FIELDS else v) for k, v in data.items()}


def _write_audit(run_id: str, request_data: dict, result: dict,
                 stdout: str = "", stderr: str = "") -> str:
    """Write audit record. Returns audit dir path."""
    audit_dir = os.path.join(AUDIT_BASE, run_id)
    os.makedirs(audit_dir, exist_ok=True)

    safe_request = _redact_body_for_log(request_data)
    with open(os.path.join(audit_dir, "request.json"), "w", encoding="utf-8") as f:
        json.dump(safe_request, f, indent=2)

    with open(os.path.join(audit_dir, "result.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)

    with open(os.path.join(audit_dir, "stdout.txt"), "w", encoding="utf-8") as f:
        f.write(stdout[:512_000])

    with open(os.path.join(audit_dir, "stderr.txt"), "w", encoding="utf-8") as f:
        f.write(stderr[:128_000])

    proof_md = (
        f"# rootd Audit Record\n\n"
        f"**Run ID:** {run_id}\n"
        f"**Timestamp:** {datetime.now(timezone.utc).isoformat()}\n"
        f"**Command:** {safe_request.get('command', 'unknown')}\n"
        f"**Result:** {'PASS' if result.get('ok') else 'FAIL'}\n"
        f"**Exit code:** {result.get('exit_code', 'N/A')}\n\n"
        f"## Policy\n\n"
        f"- Tier: {result.get('tier', 'unknown')}\n"
        f"- Allowed: {result.get('policy_allowed', False)}\n\n"
        f"## Artifacts\n\n"
        f"- request.json (sensitive fields redacted)\n"
        f"- result.json\n"
        f"- stdout.txt\n"
        f"- stderr.txt\n"
    )
    with open(os.path.join(audit_dir, "PROOF.md"), "w", encoding="utf-8") as f:
        f.write(proof_md)

    return audit_dir


def _exec_systemctl(subcmd: str, unit: str) -> tuple[int, str, str]:
    """Execute a systemctl subcommand on a validated unit."""
    allowed = ["restart", "enable", "start", "stop", "status"]
    if subcmd not in allowed:
        return (1, "", f"systemctl subcommand '{subcmd}' not allowed")
    args = ["systemctl", subcmd, unit]
    if subcmd == "enable":
        args = ["systemctl", "enable", "--now", unit]
    try:
        proc = subprocess.run(args, capture_output=True, timeout=30, text=True)
        return (proc.returncode, proc.stdout, proc.stderr)
    except subprocess.TimeoutExpired:
        return (1, "", f"systemctl {subcmd} {unit} timed out (30s)")
    except Exception as e:
        return (1, "", str(e))


def _exec_systemctl_daemon_reload() -> tuple[int, str, str]:
    """Reload systemd daemon."""
    try:
        proc = subprocess.run(
            ["systemctl", "daemon-reload"],
            capture_output=True, timeout=15, text=True,
        )
        return (proc.returncode, proc.stdout, proc.stderr)
    except Exception as e:
        return (1, "", str(e))


def _exec_tailscale_serve(subcmd: str, args_dict: dict) -> tuple[int, str, str]:
    """Execute tailscale serve commands."""
    if subcmd == "reset":
        cmd = ["tailscale", "serve", "reset"]
    elif subcmd == "apply":
        target = args_dict.get("target", "")
        if not target:
            return (1, "", "Missing target for tailscale serve apply")
        tcp_port = args_dict.get("tcp_port", "443")
        cmd = ["tailscale", "serve", "--bg", f"--tcp={tcp_port}", target]
    elif subcmd == "status":
        cmd = ["tailscale", "serve", "status", "--json"]
    else:
        return (1, "", f"tailscale serve subcommand '{subcmd}' not allowed")
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=30, text=True)
        return (proc.returncode, proc.stdout, proc.stderr)
    except subprocess.TimeoutExpired:
        return (1, "", f"tailscale serve {subcmd} timed out (30s)")
    except Exception as e:
        return (1, "", str(e))


def _exec_write_etc_config(path: str, content: str) -> tuple[int, str, str]:
    """Write content to an allowlisted /etc path. Atomic via tmp+rename."""
    parent = os.path.dirname(path)
    try:
        os.makedirs(parent, mode=0o755, exist_ok=True)
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(content)
        os.chmod(tmp_path, 0o644)
        os.replace(tmp_path, path)
        return (0, f"Wrote {len(content)} bytes to {path}", "")
    except Exception as e:
        return (1, "", str(e))


def _exec_bind_verify_443() -> tuple[int, str, str]:
    """Verify that something is listening on 443."""
    try:
        proc = subprocess.run(
            ["ss", "-tlnp"],
            capture_output=True, timeout=5, text=True,
        )
        lines = proc.stdout.strip().split("\n")
        for line in lines:
            if ":443 " in line or ":443\t" in line:
                return (0, f"Port 443 is bound:\n{line}", "")
        return (1, "", "Nothing listening on port 443")
    except Exception as e:
        return (1, "", str(e))


def _exec_install_timer(service_file: str, timer_file: str, unit_name: str) -> tuple[int, str, str]:
    """Install a systemd timer+service from repo paths."""
    if ".." in service_file or ".." in timer_file:
        return (1, "", "Path traversal rejected")
    svc_src = os.path.join(ROOT_DIR, service_file)
    tmr_src = os.path.join(ROOT_DIR, timer_file)
    if not os.path.isfile(svc_src):
        return (1, "", f"Service file not found: {svc_src}")
    if not os.path.isfile(tmr_src):
        return (1, "", f"Timer file not found: {tmr_src}")

    svc_dst = f"/etc/systemd/system/{os.path.basename(service_file)}"
    tmr_dst = f"/etc/systemd/system/{os.path.basename(timer_file)}"

    try:
        with open(svc_src, "r") as f:
            svc_content = f.read().replace("/opt/ai-ops-runner", ROOT_DIR)
        with open(svc_dst, "w") as f:
            f.write(svc_content)

        with open(tmr_src, "r") as f:
            tmr_content = f.read()
        with open(tmr_dst, "w") as f:
            f.write(tmr_content)

        _exec_systemctl_daemon_reload()
        ec, out, err = _exec_systemctl("enable", os.path.basename(timer_file))
        return (ec, f"Installed {svc_dst} + {tmr_dst}\n{out}", err)
    except Exception as e:
        return (1, "", str(e))


def handle_command(data: dict) -> dict:
    """Route and execute a rootd command. Returns structured result."""
    command = data.get("command", "")
    args = data.get("args", {})
    run_id = data.get("run_id") or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + os.urandom(4).hex()

    if not isinstance(args, dict):
        args = {}

    policy_result = _evaluator.validate_rootd_command(command, args)
    if not policy_result.allowed:
        result = {
            "ok": False,
            "command": command,
            "run_id": run_id,
            "tier": policy_result.tier,
            "policy_allowed": False,
            "reason": policy_result.reason,
            "exit_code": None,
        }
        _write_audit(run_id, data, result)
        return result

    exit_code, stdout, stderr = 1, "", "Unknown command"

    if command == "systemctl_restart":
        exit_code, stdout, stderr = _exec_systemctl("restart", args.get("unit", ""))
    elif command == "systemctl_enable":
        exit_code, stdout, stderr = _exec_systemctl("enable", args.get("unit", ""))
    elif command == "tailscale_serve":
        subcmd = args.get("subcmd", "status")
        exit_code, stdout, stderr = _exec_tailscale_serve(subcmd, args)
    elif command == "write_etc_config":
        exit_code, stdout, stderr = _exec_write_etc_config(
            args.get("path", ""), args.get("content", "")
        )
    elif command == "bind_verify_443":
        exit_code, stdout, stderr = _exec_bind_verify_443()
    elif command == "install_timer":
        exit_code, stdout, stderr = _exec_install_timer(
            args.get("service_file", ""),
            args.get("timer_file", ""),
            args.get("unit_name", ""),
        )
    elif command == "daemon_reload":
        exit_code, stdout, stderr = _exec_systemctl_daemon_reload()
    else:
        stderr = f"Command '{command}' not implemented"

    result = {
        "ok": exit_code == 0,
        "command": command,
        "run_id": run_id,
        "tier": policy_result.tier,
        "policy_allowed": True,
        "exit_code": exit_code,
        "stdout": stdout[:64_000],
        "stderr": stderr[:16_000],
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }

    _write_audit(run_id, data, result, stdout, stderr)
    return result


class UnixHTTPServer(HTTPServer):
    address_family = socket.AF_UNIX

    def server_bind(self):
        if os.path.exists(self.server_address):
            os.unlink(self.server_address)
        socket_dir = os.path.dirname(self.server_address)
        os.makedirs(socket_dir, mode=0o755, exist_ok=True)
        super().server_bind()
        os.chmod(self.server_address, 0o660)

    def server_close(self):
        super().server_close()
        try:
            os.unlink(self.server_address)
        except OSError:
            pass


class RootdHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write(f"[rootd] {self.command} {args[0] if args else ''}\n")

    def send_json(self, status: int, body: dict) -> None:
        raw = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == "/health":
            self.send_json(200, {
                "ok": True,
                "version": VERSION,
                "time": datetime.now(timezone.utc).isoformat(),
                "socket": SOCKET_PATH,
            })
            return
        if self.path == "/policy":
            self.send_json(200, _evaluator.to_summary())
            return
        self.send_json(404, {"error": "Not found"})

    def do_POST(self):
        if self.path != "/exec":
            self.send_json(404, {"error": "Not found"})
            return

        content_length = self.headers.get("Content-Length")
        if not content_length or int(content_length) > MAX_REQUEST_SIZE:
            self.send_json(400, {"error": "Invalid or oversized body"})
            return

        body_raw = self.rfile.read(int(content_length))
        signature = self.headers.get("X-RootD-HMAC", "")

        if not signature or not _verify_hmac(body_raw, signature):
            self.send_json(403, {"error": "Invalid or missing HMAC signature"})
            return

        try:
            data = json.loads(body_raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self.send_json(400, {"error": "Invalid JSON"})
            return

        if not isinstance(data, dict):
            self.send_json(400, {"error": "Body must be JSON object"})
            return

        command = data.get("command")
        if not command or not isinstance(command, str):
            self.send_json(400, {"error": "Missing or invalid 'command' field"})
            return

        result = handle_command(data)
        status_code = 200 if result.get("ok") else 403 if not result.get("policy_allowed") else 500
        self.send_json(status_code, result)


def main() -> int:
    if os.geteuid() != 0:
        sys.stderr.write("[rootd] FATAL: rootd must run as root (euid=0)\n")
        return 1

    hmac_key = _load_hmac_key()
    if not hmac_key:
        sys.stderr.write(f"[rootd] FATAL: HMAC key not found or too short at {HMAC_KEY_PATH}\n")
        sys.stderr.write(f"[rootd] Generate with: openssl rand -hex 32 | sudo tee {HMAC_KEY_PATH}\n")
        return 1

    os.makedirs(AUDIT_BASE, exist_ok=True)

    def handle_signal(signum, frame):
        sys.stderr.write(f"[rootd] Received signal {signum}, shutting down\n")
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    server = UnixHTTPServer(SOCKET_PATH, RootdHandler)
    sys.stderr.write(f"[rootd] listening on {SOCKET_PATH}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
