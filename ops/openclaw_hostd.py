#!/usr/bin/env python3
"""
openclaw_hostd — Host-local allowlisted executor. No SSH.
Binds 127.0.0.1:8877 only. Auth via X-OpenClaw-Admin-Token from
/etc/ai-ops-runner/secrets/openclaw_admin_token. Fail-closed everywhere.
Console reaches hostd via host network (network_mode: host).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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


def _load_allowlist_from_registry() -> dict | None:
    """Load allowlist from config/action_registry.json. Returns None on missing/error."""
    registry_path = os.path.join(ROOT_DIR, "config", "action_registry.json")
    if not os.path.isfile(registry_path):
        return None
    try:
        with open(registry_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        actions = data.get("actions") or []
        out: dict = {}
        for a in actions:
            aid = a.get("id")
            if not aid or not isinstance(aid, str):
                continue
            template = a.get("cmd_template")
            timeout = a.get("timeout_sec", 60)
            if not template or not isinstance(template, str):
                continue
            cmd_str = template.replace("${ROOT_DIR}", ROOT_DIR)
            out[aid] = {"cmd": ["bash", "-c", cmd_str], "timeout_sec": int(timeout)}
            for alias in a.get("aliases") or []:
                if isinstance(alias, str) and alias:
                    out[alias] = out[aid]
        return out if out else None
    except (OSError, json.JSONDecodeError) as e:
        sys.stderr.write(f"[hostd] Failed to load registry: {e}\n")
        return None


def _fallback_allowlist() -> dict:
    """Minimal allowlist when config/action_registry.json is missing/corrupt."""
    return {
        "doctor": {"cmd": ["bash", "-c", f"cd {ROOT_DIR} && ./ops/openclaw_doctor.sh"], "timeout_sec": 180},
        "apply": {"cmd": ["bash", "-c", f"cd {ROOT_DIR} && ./ops/openclaw_apply_remote.sh"], "timeout_sec": 120},
        "guard": {"cmd": ["bash", "-c", f"cd {ROOT_DIR} && sudo ./ops/openclaw_install_guard.sh"], "timeout_sec": 30},
        "port_audit": {"cmd": ["bash", "-c", f"cd {ROOT_DIR} && ./ops/show_port_audit.sh"], "timeout_sec": 60},
        "tail_guard_log": {"cmd": ["bash", "-c", "journalctl -u openclaw-guard.service -n 200 --no-pager"], "timeout_sec": 30},
        "timer": {"cmd": ["systemctl", "status", "openclaw-guard.timer", "--no-pager"], "timeout_sec": 10},
    }


reg = _load_allowlist_from_registry()
ALLOWLIST = reg if reg is not None else _fallback_allowlist()

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
    with open(os.path.join(blocked_dir, "SUMMARY.md"), "w", encoding="utf-8") as f:
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


SECRETS_ALLOWLIST_PATH = os.path.join(ROOT_DIR, "configs", "secrets_allowlist.json")
MAX_SECRET_UPLOAD_BODY = 200_000  # ~128KB base64 + JSON overhead


def _load_secrets_allowlist() -> tuple[dict[str, str], int]:
    """Return (uploads: {filename -> absolute_path}, max_size_bytes). Default 128KB."""
    default_max = 131072
    default_uploads = {"gmail_client.json": "/etc/ai-ops-runner/secrets/soma_kajabi/gmail_client.json"}
    if not os.path.isfile(SECRETS_ALLOWLIST_PATH):
        return (default_uploads, default_max)
    try:
        with open(SECRETS_ALLOWLIST_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        uploads = data.get("uploads")
        if not isinstance(uploads, dict):
            return (default_uploads, default_max)
        out = {}
        for k, v in uploads.items():
            if isinstance(k, str) and isinstance(v, str) and k and v and ".." not in k:
                out[k] = os.path.normpath(v)
        max_size = int(data.get("max_size_bytes", default_max)) if isinstance(data.get("max_size_bytes"), (int, float)) else default_max
        return (out if out else default_uploads, max(1, min(max_size, 131072)))
    except (OSError, json.JSONDecodeError):
        return (default_uploads, default_max)


def _redact_path(p: str) -> str:
    """Redact path for response; show base dir only."""
    base = "/etc/ai-ops-runner/secrets"
    if p.startswith(base):
        return base + "/…"
    return "/…"


def _fingerprint_prefix(content: bytes) -> str:
    """Return first 8 chars of sha256 hex (no secrets in value)."""
    return hashlib.sha256(content).hexdigest()[:8]


def _gmail_client_secret_status() -> dict:
    """Return { exists: bool, fingerprint: str | null }. No secrets."""
    uploads, _ = _load_secrets_allowlist()
    path = uploads.get("gmail_client.json")
    if not path or not os.path.isfile(path):
        return {"exists": False, "fingerprint": None}
    try:
        with open(path, "rb") as f:
            raw = f.read()
        return {"exists": True, "fingerprint": _fingerprint_prefix(raw)}
    except OSError:
        return {"exists": False, "fingerprint": None}


def handle_secrets_upload(body_raw: bytes) -> tuple[int, dict]:
    """Validate and write allowlisted secret. Return (status_code, json_body). Never log content."""
    try:
        data = json.loads(body_raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return (400, {"ok": False, "error": "Invalid JSON"})
    if not isinstance(data, dict):
        return (400, {"ok": False, "error": "Body must be object"})
    filename = data.get("filename")
    content_b64 = data.get("content")
    if not filename or not isinstance(filename, str):
        return (400, {"ok": False, "error": "Missing filename"})
    if not content_b64 or not isinstance(content_b64, str):
        return (400, {"ok": False, "error": "Missing content (base64)"})
    uploads, max_size = _load_secrets_allowlist()
    if filename not in uploads:
        return (403, {"ok": False, "error": "Filename not allowlisted"})
    target_path = uploads[filename]
    try:
        content = base64.b64decode(content_b64, validate=True)
    except Exception:
        return (400, {"ok": False, "error": "Invalid base64"})
    if len(content) > max_size:
        return (400, {"ok": False, "error": f"Content exceeds {max_size} bytes"})
    try:
        parsed = json.loads(content.decode("utf-8"))
        if not isinstance(parsed, dict):
            return (400, {"ok": False, "error": "Content must be JSON object"})
    except (json.JSONDecodeError, UnicodeDecodeError):
        return (400, {"ok": False, "error": "Content is not valid JSON"})
    parent = os.path.dirname(target_path)
    try:
        os.makedirs(parent, mode=0o700, exist_ok=True)
    except OSError as e:
        return (500, {"ok": False, "error": "Cannot create secrets dir"})
    fd, tmp = tempfile.mkstemp(dir=parent, prefix=".upload.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
        os.chmod(tmp, 0o600)
        os.replace(tmp, target_path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return (500, {"ok": False, "error": "Write failed"})
    fingerprint = _fingerprint_prefix(content)
    return (200, {
        "ok": True,
        "saved_path": _redact_path(target_path),
        "fingerprint": fingerprint,
        "next_steps": [
            "Run Gmail Connect (start → enter user_code at verification URL → finalize).",
            "Then run Phase 0 to populate artifacts.",
        ],
    })


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


def _write_error_json(art_dir: str, error_class: str, reason: str,
                      recommended_next_action: str,
                      underlying_exception: str | None = None) -> None:
    """Always write a structured error.json artifact on any failure."""
    try:
        error_obj: dict = {
            "error_class": error_class,
            "reason": reason,
            "recommended_next_action": recommended_next_action,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        }
        if underlying_exception:
            error_obj["underlying_exception"] = underlying_exception[:2000]
        with open(os.path.join(art_dir, "error.json"), "w", encoding="utf-8") as f:
            json.dump(error_obj, f, indent=2)
    except OSError:
        pass


def run_action(action: str, run_id: str, params: dict | None = None) -> tuple[int, str, str, bool]:
    """Run allowlisted action. Returns (exit_code, stdout, stderr, truncated).
    Always writes stdout.txt, stderr.txt, hostd_result.json, and error.json (on failure).
    For code.opencode.propose_patch, params (goal, ref, test_command, dry_run) are written to params.json."""
    if action not in ALLOWLIST:
        return (-1, "", f"Action not in allowlist: {action}", False)
    spec = ALLOWLIST[action]
    cmd = spec["cmd"]
    timeout_sec = spec["timeout_sec"]
    started_at = datetime.now(timezone.utc).isoformat()
    art_dir = os.path.join(ROOT_DIR, ARTIFACTS_HOSTD, run_id)
    os.makedirs(art_dir, exist_ok=True)
    if params and action == "code.opencode.propose_patch":
        allowed_params = {"goal", "ref", "test_command", "dry_run"}
        filtered = {k: v for k, v in params.items() if k in allowed_params and isinstance(v, (str, bool, int, float))}
        with open(os.path.join(art_dir, "params.json"), "w", encoding="utf-8") as f:
            json.dump(filtered, f, indent=2)
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
        if proc.returncode != 0:
            stderr_tail = stderr_s.strip().split("\n")[-3:]
            error_class = "action_nonzero_exit"
            # Parse error_class from stdout (project actions print JSON)
            if not stderr_s.strip() and stdout_s.strip():
                for line in reversed(stdout_s.strip().split("\n")):
                    line = line.strip()
                    if line.startswith("{"):
                        try:
                            parsed = json.loads(line)
                            if parsed.get("error_class"):
                                error_class = parsed["error_class"]
                                break
                        except (json.JSONDecodeError, TypeError):
                            pass
            _write_error_json(
                art_dir,
                error_class=error_class,
                reason=f"Action '{action}' exited with code {proc.returncode}",
                recommended_next_action=f"Check stderr in {ARTIFACTS_HOSTD}/{run_id}/stderr.txt",
                underlying_exception="\n".join(stderr_tail) if stderr_tail else None,
            )
            # When stderr empty, write ERROR_SUMMARY.txt from stdout classification
            if not stderr_s.strip() and stdout_s.strip():
                try:
                    summary_path = os.path.join(art_dir, "ERROR_SUMMARY.txt")
                    with open(summary_path, "w", encoding="utf-8") as f:
                        f.write(f"error_class: {error_class}\n")
                        f.write(f"action: {action}\n")
                        f.write(f"exit_code: {proc.returncode}\n")
                        f.write("---\n")
                        f.write(stdout_s[-4000:] if len(stdout_s) > 4000 else stdout_s)
                except OSError:
                    pass
            # Ensure hostd_result.json has error_class for UI
            result["error_class"] = error_class
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
        _write_error_json(
            art_dir,
            error_class="action_timeout",
            reason=f"Action '{action}' timed out after {timeout_sec}s",
            recommended_next_action="Increase timeout_sec in action_registry.json or investigate slow execution",
        )
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
            _write_error_json(
                art_dir,
                error_class="action_exception",
                reason=f"Action '{action}' raised an exception",
                recommended_next_action="Check hostd logs and stderr artifact",
                underlying_exception=err_s,
            )
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

    def _require_admin(self) -> bool:
        """Return True if admin token present and matches. Sends 403/503 and returns False otherwise."""
        token = self.headers.get("X-OpenClaw-Admin-Token") or ""
        expected = load_admin_token()
        if expected is None:
            self.send_json(503, {"error": "admin not configured"})
            return False
        if not token or not constant_time_compare(token, expected):
            self.send_json(403, {"error": "Forbidden"})
            return False
        return True

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
        if parsed.path == "/connectors/gmail/secret-status":
            if not self._require_admin():
                return
            self.send_json(200, _gmail_client_secret_status())
            return
        self.send_json(404, {"error": "Not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/secrets/upload":
            if not self._require_admin():
                return
            content_length = self.headers.get("Content-Length")
            if not content_length or int(content_length) > MAX_SECRET_UPLOAD_BODY:
                self.send_json(400, {"ok": False, "error": "Invalid or missing body"})
                return
            body = self.rfile.read(int(content_length))
            status, resp = handle_secrets_upload(body)
            self.send_json(status, resp)
            return
        if parsed.path != "/exec":
            self.send_json(404, {"error": "Not found"})
            return
        if not self._require_admin():
            return
        content_length = self.headers.get("Content-Length")
        if not content_length or int(content_length) > 65536:
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
        params = data.get("params") if isinstance(data.get("params"), dict) else None
        if action == "code.opencode.propose_patch":
            if not params or not isinstance(params.get("goal"), str) or not str(params.get("goal", "")).strip():
                self.send_json(400, {"error": "code.opencode.propose_patch requires params.goal (non-empty string)"})
                return
        run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + os.urandom(4).hex()
        # Soma-first gate: block orb.backtest.* until baseline PASS and gate unlocked
        if action in ORB_BACKTEST_ACTIONS:
            allowed, _reason = is_orb_backtest_allowed()
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
        exit_code, stdout, stderr, truncated = run_action(action, run_id, params)
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
    server = ThreadingHTTPServer((HOST, PORT), Handler)
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
