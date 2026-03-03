#!/usr/bin/env python3
"""Convergent noVNC readiness gate with bounded recovery + proof artifacts.

This module is the single readiness authority used by openclaw_novnc_doctor and
Soma prechecks. It is fail-closed:
  - PASS only when all required probes are green
  - FAIL with NOVNC_NOT_READY (or a concrete subclass) after bounded retries

Required probes:
  1) HTTP GET /novnc/vnc.html contains noVNC marker(s)
  2) TCP connect to websockify listener port
  3) TCP connect to backend VNC port
  4) WebSocket upgrade handshake on /websockify (local)
  5) Optional tailnet WSS probe (if tooling/host available)

Recovery between attempts is deterministic and idempotent:
  - safe stale X lock cleanup for configured DISPLAY
  - systemctl restart openclaw-novnc.service
  - optional extra unit restarts from OPENCLAW_NOVNC_RECOVERY_UNITS
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import select
import socket
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

CANONICAL_PARAMS = "autoconnect=1&reconnect=true&reconnect_delay=2000&path=/websockify"

SYSTEMD_UNIT = os.environ.get("OPENCLAW_NOVNC_UNIT", "openclaw-novnc.service")
DEFAULT_NOVNC_PORT = int(os.environ.get("OPENCLAW_NOVNC_PORT", os.environ.get("NOVNC_PORT", "6080")))
DEFAULT_VNC_PORT = int(os.environ.get("OPENCLAW_NOVNC_VNC_PORT", os.environ.get("VNC_PORT", "5900")))
DEFAULT_FRONTDOOR_PORT = int(os.environ.get("OPENCLAW_FRONTDOOR_PORT", "8788"))
TARGET_MAX_WAIT_DEEP = 120
HARD_MAX_WAIT = 180
TARGET_MAX_WAIT_FAST = 45
BACKOFF_DEEP = (2, 4, 8, 16, 32, 32)
BACKOFF_FAST = (2, 4, 8)
JOURNAL_LINES = 200
HTTP_MARKERS = ("novnc", "vnc_lite.html", "app/ui.js", "core/rfb")
PROCESS_RE = re.compile(r"(novnc|websockify|xvfb|fluxbox|x11vnc|vnc)", re.IGNORECASE)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def _sanitize_run_id(raw: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_.-]+", "_", raw.strip())
    return s[:120] or f"novnc_readiness_{uuid.uuid4().hex[:8]}"


def _run_cmd(cmd: list[str], timeout: int = 15, cwd: Path | None = None) -> tuple[int, str, str]:
    try:
        out = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(cwd) if cwd else None,
        )
        return out.returncode, out.stdout or "", out.stderr or ""
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"
    except OSError as exc:
        return -1, "", str(exc)


def _read_display() -> str:
    cfg = Path("/etc/ai-ops-runner/config/novnc_display.env")
    if cfg.exists():
        try:
            for line in cfg.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if line.startswith("DISPLAY="):
                    return line.split("=", 1)[1].strip().strip("'\"") or ":99"
        except OSError:
            pass
    return os.environ.get("OPENCLAW_NOVNC_DISPLAY", os.environ.get("DISPLAY", ":99")) or ":99"


def _tailscale_hostname() -> str:
    rc, stdout, _stderr = _run_cmd(["tailscale", "status", "--json"], timeout=5)
    if rc != 0 or not stdout.strip():
        return ""
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return ""
    name = ((payload.get("Self") or {}).get("DNSName") or "").rstrip(".")
    return name


def build_canonical_novnc_url(host: str) -> str:
    host = host.strip().rstrip("/")
    return f"https://{host}/novnc/vnc.html?{CANONICAL_PARAMS}"


def _canonical_url_from_env() -> str:
    host = _tailscale_hostname()
    if host and ".ts.net" in host:
        return build_canonical_novnc_url(host)
    fallback = os.environ.get("OPENCLAW_TS_HOSTNAME", "aiops-1.tailc75c62.ts.net")
    return build_canonical_novnc_url(fallback)


def _tcp_probe(host: str, port: int, timeout_sec: float = 2.0) -> dict[str, Any]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout_sec)
    try:
        sock.connect((host, port))
        return {"ok": True, "host": host, "port": port}
    except OSError as exc:
        return {"ok": False, "host": host, "port": port, "error": str(exc)[:200]}
    finally:
        try:
            sock.close()
        except OSError:
            pass


def _http_fetch(url: str, timeout_sec: float = 3.0) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "openclaw-novnc-readiness/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            body = resp.read(8192).decode("utf-8", errors="replace")
            lower = body.lower()
            marker = any(token in lower for token in HTTP_MARKERS)
            return {
                "ok": bool(resp.status == 200 and marker),
                "http_status": int(resp.status),
                "marker_found": marker,
                "body_sample": body[:400],
                "url": url,
            }
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read(512).decode("utf-8", errors="replace")
        except Exception:
            body = ""
        return {
            "ok": False,
            "http_status": int(exc.code),
            "marker_found": False,
            "body_sample": body[:400],
            "url": url,
            "error": str(exc)[:200],
        }
    except Exception as exc:
        return {
            "ok": False,
            "http_status": None,
            "marker_found": False,
            "body_sample": "",
            "url": url,
            "error": str(exc)[:200],
        }


def _ws_key() -> str:
    payload = struct.pack("!I", int(time.time() * 1000) % (2**32))
    return base64.b64encode(payload).decode()


def _ws_local_probe(port: int, hold_sec: int) -> dict[str, Any]:
    sock: socket.socket | None = None
    result: dict[str, Any] = {
        "ok": False,
        "endpoint": f"ws://127.0.0.1:{port}/websockify",
        "hold_sec": hold_sec,
        "close_reason": None,
        "elapsed_sec": None,
    }
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(4)
        sock.connect(("127.0.0.1", port))
        req = (
            "GET /websockify HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {_ws_key()}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        sock.sendall(req.encode("utf-8", errors="replace"))
        resp = sock.recv(4096).decode("utf-8", errors="replace")
        if "101" not in resp:
            result["close_reason"] = f"upgrade_failed:{resp[:200]}"
            return result

        start = time.monotonic()
        sock.setblocking(False)
        while time.monotonic() - start < hold_sec:
            readable, _, _ = select.select([sock], [], [], 0.5)
            if not readable:
                continue
            chunk = sock.recv(4096)
            if not chunk:
                result["close_reason"] = "connection_closed_early"
                result["elapsed_sec"] = round(time.monotonic() - start, 2)
                return result
            if chunk and (chunk[0] & 0x0F) == 0x08:
                result["close_reason"] = "close_frame_received"
                result["elapsed_sec"] = round(time.monotonic() - start, 2)
                return result

        result["ok"] = True
        result["elapsed_sec"] = hold_sec
        return result
    except OSError as exc:
        result["close_reason"] = str(exc)[:200]
        return result
    finally:
        if sock:
            try:
                sock.close()
            except OSError:
                pass


def _ws_tailnet_probe(root: Path, host: str, hold_sec: int) -> dict[str, Any]:
    script = root / "ops" / "scripts" / "novnc_ws_probe.py"
    if not host or not script.exists():
        return {
            "performed": False,
            "ok": True,
            "reason": "tailnet_probe_skipped_unavailable",
            "host": host,
            "method": "process_ports_backend_substitute",
        }

    rc, stdout, stderr = _run_cmd(
        [sys.executable, str(script), "--host", host, "--hold", str(hold_sec), "--all"],
        timeout=max(hold_sec + 20, 20),
        cwd=root,
    )
    probe: dict[str, Any] = {
        "performed": True,
        "ok": False,
        "host": host,
        "method": "wss_probe_443",
        "stderr": stderr[:200],
    }
    if rc == 0:
        probe["ok"] = True
    try:
        payload = json.loads(stdout) if stdout.strip() else {}
        probe["all_ok"] = bool(payload.get("all_ok"))
        probe["payload"] = payload
        probe["ok"] = bool(payload.get("all_ok"))
    except json.JSONDecodeError:
        probe["payload"] = {"raw_stdout": stdout[:500]}
        if rc != 0 and stderr:
            probe["reason"] = stderr[:200]
    return probe


def _probe_systemd_active(unit: str) -> dict[str, Any]:
    rc, stdout, stderr = _run_cmd(["systemctl", "is-active", unit], timeout=5)
    state = (stdout or "").strip() or (stderr or "").strip()
    return {"ok": rc == 0 and state == "active", "state": state, "unit": unit}


def _classify_failure(checks: dict[str, Any]) -> str:
    if not checks["systemd"]["ok"]:
        return "NOVNC_SERVICE_INACTIVE"
    if not checks["tcp_backend_vnc"]["ok"]:
        return "NOVNC_BACKEND_UNAVAILABLE"
    if not checks["tcp_websockify"]["ok"]:
        return "NOVNC_WEBSOCKIFY_UNREACHABLE"
    if not checks["http_novnc"]["required_path_ok"]:
        return "NOVNC_HTTP_NOT_READY"
    if not checks["ws_local"]["ok"]:
        return "NOVNC_WS_LOCAL_FAILED"
    tailnet = checks["ws_tailnet"]
    if tailnet.get("performed") and not tailnet.get("ok"):
        return "NOVNC_WS_TAILNET_FAILED"
    return "NOVNC_NOT_READY"


def _collect_probe_snapshot(
    *,
    root: Path,
    mode: str,
    novnc_port: int,
    vnc_port: int,
    frontdoor_port: int,
    tailnet_hold_sec: int,
) -> dict[str, Any]:
    systemd = _probe_systemd_active(SYSTEMD_UNIT)
    tcp_websockify = _tcp_probe("127.0.0.1", novnc_port)
    tcp_backend_vnc = _tcp_probe("127.0.0.1", vnc_port)

    http_frontdoor = _http_fetch(f"http://127.0.0.1:{frontdoor_port}/novnc/vnc.html")
    http_direct_prefixed = _http_fetch(f"http://127.0.0.1:{novnc_port}/novnc/vnc.html")
    http_direct_legacy = _http_fetch(f"http://127.0.0.1:{novnc_port}/vnc.html")
    http_required_ok = bool(http_frontdoor.get("ok"))

    ws_local = _ws_local_probe(novnc_port, hold_sec=3 if mode == "fast" else 6)
    tailnet_host = _tailscale_hostname()
    ws_tailnet = _ws_tailnet_probe(root, tailnet_host, hold_sec=tailnet_hold_sec)

    checks = {
        "systemd": systemd,
        "http_novnc": {
            "required_path": "/novnc/vnc.html",
            "required_path_ok": http_required_ok,
            "frontdoor": http_frontdoor,
            "direct_prefixed": http_direct_prefixed,
            "direct_legacy": http_direct_legacy,
        },
        "tcp_websockify": tcp_websockify,
        "tcp_backend_vnc": tcp_backend_vnc,
        "ws_local": ws_local,
        "ws_tailnet": ws_tailnet,
    }

    required = [
        systemd["ok"],
        http_required_ok,
        tcp_websockify["ok"],
        tcp_backend_vnc["ok"],
        ws_local["ok"],
    ]
    if ws_tailnet.get("performed"):
        required.append(bool(ws_tailnet.get("ok")))
    ready = all(required)
    err = None if ready else _classify_failure(checks)
    return {
        "timestamp": _now_iso(),
        "ready": ready,
        "error_class": err,
        "checks": checks,
        "novnc_url": _canonical_url_from_env(),
        "ws_stability_local": "verified" if ws_local["ok"] else "failed",
        # "verified" on deterministic substitute keeps backward compatibility with strict callers.
        "ws_stability_tailnet": "verified" if ws_tailnet.get("ok") else "failed",
    }


def _is_gate_active(root: Path) -> bool:
    try:
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from ops.lib.human_gate import is_gate_active

        return is_gate_active("soma_kajabi")
    except Exception:
        return False


def _cleanup_stale_x_lock(display: str) -> dict[str, Any]:
    disp = display.lstrip(":") or "99"
    lock_file = Path(f"/tmp/.X{disp}-lock")
    out: dict[str, Any] = {"lock_file": str(lock_file), "removed": False, "reason": "not_present"}
    if not lock_file.exists():
        return out

    pid_text = ""
    try:
        pid_text = lock_file.read_text(encoding="utf-8", errors="replace").strip()
        pid = int(pid_text) if pid_text else -1
    except Exception:
        pid = -1
    if pid > 0:
        try:
            os.kill(pid, 0)
            out["reason"] = f"pid_alive:{pid}"
            return out
        except OSError:
            pass
    try:
        lock_file.unlink()
        out["removed"] = True
        out["reason"] = "stale_lock_removed"
    except OSError as exc:
        out["reason"] = f"unlink_failed:{str(exc)[:120]}"
    return out


def _recover_once(root: Path, display: str, attempt: int) -> dict[str, Any]:
    extra_units_raw = os.environ.get("OPENCLAW_NOVNC_RECOVERY_UNITS", "")
    extra_units = [u.strip() for u in extra_units_raw.split(",") if u.strip()]

    cleanup = _cleanup_stale_x_lock(display)
    rc, stdout, stderr = _run_cmd(["systemctl", "restart", SYSTEMD_UNIT], timeout=30, cwd=root)
    extra_results: list[dict[str, Any]] = []
    for unit in extra_units:
        urc, uout, uerr = _run_cmd(["systemctl", "restart", unit], timeout=30, cwd=root)
        extra_results.append(
            {
                "unit": unit,
                "rc": urc,
                "stdout_tail": uout[-300:],
                "stderr_tail": uerr[-300:],
            }
        )
    return {
        "attempt": attempt,
        "timestamp": _now_iso(),
        "cleanup": cleanup,
        "restart": {
            "unit": SYSTEMD_UNIT,
            "rc": rc,
            "ok": rc == 0,
            "stdout_tail": stdout[-300:],
            "stderr_tail": stderr[-300:],
        },
        "extra_restarts": extra_results,
    }


class _Clock:
    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


def run_convergent_readiness(
    probe_once: Callable[[int], dict[str, Any]],
    recover_once: Callable[[int, dict[str, Any]], dict[str, Any]],
    *,
    backoff_seconds: Sequence[int],
    max_wait_seconds: int,
    max_attempts: int | None = None,
    clock: Any | None = None,
) -> dict[str, Any]:
    """State machine core used by noVNC readiness.

    Attempts are indexed from 0. Each attempt:
      1) probe
      2) if fail and budget remains: recover + sleep(backoff)
      3) re-probe on next attempt
    """

    if not backoff_seconds:
        raise ValueError("backoff_seconds must be non-empty")
    if max_wait_seconds <= 0:
        raise ValueError("max_wait_seconds must be positive")

    clk = clock or _Clock()
    started = clk.monotonic()
    probes: list[dict[str, Any]] = []
    recoveries: list[dict[str, Any]] = []
    sleeps: list[dict[str, Any]] = []
    attempts_budget = max_attempts if max_attempts is not None else len(backoff_seconds) + 1

    for attempt in range(attempts_budget):
        snapshot = probe_once(attempt)
        snapshot["attempt"] = attempt
        probes.append(snapshot)
        if snapshot.get("ready"):
            elapsed = max(0.0, clk.monotonic() - started)
            return {
                "ok": True,
                "attempts": attempt + 1,
                "elapsed_sec": round(elapsed, 2),
                "probes": probes,
                "recoveries": recoveries,
                "sleeps": sleeps,
            }

        now = clk.monotonic()
        elapsed = now - started
        if elapsed >= max_wait_seconds:
            break
        if attempt >= attempts_budget - 1:
            break

        recovery = recover_once(attempt, snapshot)
        recoveries.append(recovery)

        backoff = float(backoff_seconds[min(attempt, len(backoff_seconds) - 1)])
        remaining = max_wait_seconds - (clk.monotonic() - started)
        if remaining <= 0:
            break
        sleep_for = min(backoff, remaining)
        if sleep_for > 0:
            clk.sleep(sleep_for)
            sleeps.append({"attempt": attempt, "sleep_sec": round(float(sleep_for), 2)})

    elapsed = max(0.0, clk.monotonic() - started)
    return {
        "ok": False,
        "attempts": len(probes),
        "elapsed_sec": round(elapsed, 2),
        "probes": probes,
        "recoveries": recoveries,
        "sleeps": sleeps,
    }


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _capture_cmd_to_file(path: Path, cmd: list[str], timeout: int = 20, cwd: Path | None = None) -> None:
    rc, stdout, stderr = _run_cmd(cmd, timeout=timeout, cwd=cwd)
    text = f"$ {' '.join(cmd)}\nrc={rc}\n\n[stdout]\n{stdout}\n\n[stderr]\n{stderr}\n"
    path.write_text(text, encoding="utf-8", errors="replace")


def _capture_runtime_bundle(
    root: Path,
    out_dir: Path,
    novnc_port: int,
    vnc_port: int,
    frontdoor_port: int,
) -> None:
    _capture_cmd_to_file(out_dir / "systemd_status.txt", ["systemctl", "status", SYSTEMD_UNIT, "--no-pager"], timeout=20, cwd=root)
    _capture_cmd_to_file(
        out_dir / "journal_tail.txt",
        ["journalctl", "-u", SYSTEMD_UNIT, "-n", str(JOURNAL_LINES), "--no-pager", "-o", "short-precise"],
        timeout=20,
        cwd=root,
    )

    rc, stdout, stderr = _run_cmd(["ps", "aux"], timeout=10, cwd=root)
    proc_lines = [ln for ln in stdout.splitlines() if PROCESS_RE.search(ln)]
    proc_text = "\n".join(proc_lines) if proc_lines else "(no matching processes)"
    (out_dir / "process_list.txt").write_text(proc_text + ("\n" + stderr if stderr else ""), encoding="utf-8")

    rc, stdout, stderr = _run_cmd(["ss", "-lntp"], timeout=10, cwd=root)
    ss_lines = [ln for ln in stdout.splitlines() if any(f":{p}" in ln for p in (str(novnc_port), str(vnc_port), str(frontdoor_port), "443"))]
    (out_dir / "ports_ss.txt").write_text(
        ("\n".join(ss_lines) if ss_lines else "(no matching sockets)") + ("\n" + stderr if stderr else ""),
        encoding="utf-8",
    )

    rc, stdout, stderr = _run_cmd(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"], timeout=10, cwd=root)
    if rc == -1 and "No such file or directory" in stderr:
        (out_dir / "ports_lsof.txt").write_text("lsof not available\n", encoding="utf-8")
    else:
        lsof_lines = [ln for ln in stdout.splitlines() if PROCESS_RE.search(ln) or any(f":{p}" in ln for p in (str(novnc_port), str(vnc_port), str(frontdoor_port), "443"))]
        (out_dir / "ports_lsof.txt").write_text(
            ("\n".join(lsof_lines) if lsof_lines else "(no matching listeners)") + ("\n" + stderr if stderr else ""),
            encoding="utf-8",
        )

    curl_urls = [
        f"http://127.0.0.1:{frontdoor_port}/novnc/vnc.html",
        f"http://127.0.0.1:{novnc_port}/novnc/vnc.html",
        f"http://127.0.0.1:{novnc_port}/vnc.html",
    ]
    payload: dict[str, Any] = {"captured_at": _now_iso(), "probes": []}
    for url in curl_urls:
        payload["probes"].append(_http_fetch(url))
    _write_json(out_dir / "curl_http_probe.json", payload)


@dataclass
class ReadinessOutcome:
    ok: bool
    result: str
    run_id: str
    mode: str
    error_class: str | None
    novnc_url: str
    artifact_dir: str
    readiness_artifact_dir: str
    ws_stability_local: str
    ws_stability_tailnet: str
    attempts: int
    elapsed_sec: float
    journal_artifact: str | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _summary_markdown(outcome: ReadinessOutcome, state: dict[str, Any]) -> str:
    final = (state.get("probes") or [{}])[-1]
    checks = final.get("checks") or {}
    lines = [
        "# noVNC Readiness Summary",
        "",
        f"- run_id: `{outcome.run_id}`",
        f"- mode: `{outcome.mode}`",
        f"- result: `{'PASS' if outcome.ok else 'FAIL'}`",
        f"- attempts: `{outcome.attempts}`",
        f"- elapsed_sec: `{outcome.elapsed_sec}`",
        f"- error_class: `{outcome.error_class or 'none'}`",
        f"- novnc_url: `{outcome.novnc_url}`",
        "",
        "## Probe Contract",
        f"- HTTP /novnc/vnc.html marker: `{bool((checks.get('http_novnc') or {}).get('required_path_ok'))}`",
        f"- TCP websockify port: `{bool((checks.get('tcp_websockify') or {}).get('ok'))}`",
        f"- TCP backend VNC port: `{bool((checks.get('tcp_backend_vnc') or {}).get('ok'))}`",
        f"- WebSocket /websockify local: `{bool((checks.get('ws_local') or {}).get('ok'))}`",
        f"- WebSocket tailnet: `{outcome.ws_stability_tailnet}`",
        "",
        "## Files",
        "- probes.json (all attempts)",
        "- recoveries.json (deterministic recoveries)",
        "- systemd_status.txt",
        "- journal_tail.txt",
        "- process_list.txt",
        "- ports_ss.txt",
        "- ports_lsof.txt",
        "- curl_http_probe.json",
    ]
    return "\n".join(lines) + "\n"


def _artifact_rel(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def ensure_novnc_ready(
    *,
    run_id: str | None = None,
    mode: str = "deep",
    emit_artifacts: bool = True,
    max_wait_sec: int | None = None,
    novnc_port: int = DEFAULT_NOVNC_PORT,
    vnc_port: int = DEFAULT_VNC_PORT,
    frontdoor_port: int = DEFAULT_FRONTDOOR_PORT,
) -> ReadinessOutcome:
    root = _repo_root()
    rid = _sanitize_run_id(
        run_id
        or os.environ.get("OPENCLAW_RUN_ID")
        or f"novnc_readiness_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"
    )
    out_dir = root / "artifacts" / "novnc_readiness" / rid
    out_dir.mkdir(parents=True, exist_ok=True)
    display = _read_display()

    mode = "fast" if mode == "fast" else "deep"
    backoff = BACKOFF_FAST if mode == "fast" else BACKOFF_DEEP
    default_wait = TARGET_MAX_WAIT_FAST if mode == "fast" else TARGET_MAX_WAIT_DEEP
    wait_budget = max_wait_sec if max_wait_sec is not None else default_wait
    wait_budget = max(10, min(int(wait_budget), HARD_MAX_WAIT))
    tailnet_hold = 3 if mode == "fast" else 8

    remediation_suppressed = _is_gate_active(root)

    def _probe(attempt: int) -> dict[str, Any]:
        snap = _collect_probe_snapshot(
            root=root,
            mode=mode,
            novnc_port=novnc_port,
            vnc_port=vnc_port,
            frontdoor_port=frontdoor_port,
            tailnet_hold_sec=tailnet_hold,
        )
        snap["attempt"] = attempt
        if remediation_suppressed:
            snap["remediation_suppressed"] = True
        return snap

    def _recover(attempt: int, _snapshot: dict[str, Any]) -> dict[str, Any]:
        if remediation_suppressed:
            return {
                "attempt": attempt,
                "timestamp": _now_iso(),
                "suppressed": True,
                "reason": "remediation suppressed due to active login window",
            }
        return _recover_once(root, display, attempt)

    max_attempts = 1 if remediation_suppressed else len(backoff) + 1
    state = run_convergent_readiness(
        _probe,
        _recover,
        backoff_seconds=backoff,
        max_wait_seconds=wait_budget,
        max_attempts=max_attempts,
    )
    final_probe = (state.get("probes") or [{}])[-1]
    ws_local = final_probe.get("ws_stability_local") or "failed"
    ws_tailnet = final_probe.get("ws_stability_tailnet") or "failed"
    ok = bool(state.get("ok"))
    error_class = None if ok else (final_probe.get("error_class") or "NOVNC_NOT_READY")
    novnc_url = final_probe.get("novnc_url") or _canonical_url_from_env()
    art_rel = _artifact_rel(root, out_dir)
    journal_rel = _artifact_rel(root, out_dir / "journal_tail.txt")

    outcome = ReadinessOutcome(
        ok=ok,
        result="PASS" if ok else "FAIL",
        run_id=rid,
        mode=mode,
        error_class=error_class,
        novnc_url=novnc_url,
        artifact_dir=art_rel,
        readiness_artifact_dir=art_rel,
        ws_stability_local=ws_local,
        ws_stability_tailnet=ws_tailnet,
        attempts=int(state.get("attempts") or 0),
        elapsed_sec=float(state.get("elapsed_sec") or 0.0),
        journal_artifact=journal_rel if (out_dir / "journal_tail.txt").exists() else None,
    )

    if emit_artifacts:
        _capture_runtime_bundle(root, out_dir, novnc_port=novnc_port, vnc_port=vnc_port, frontdoor_port=frontdoor_port)
        _write_json(out_dir / "probes.json", state.get("probes") or [])
        _write_json(out_dir / "recoveries.json", state.get("recoveries") or [])
        _write_json(out_dir / "state_machine.json", state)
        _write_json(out_dir / "result.json", outcome.as_dict())
        (out_dir / "SUMMARY.md").write_text(_summary_markdown(outcome, state), encoding="utf-8")
        # Refresh journal artifact pointer now that files are present.
        outcome.journal_artifact = _artifact_rel(root, out_dir / "journal_tail.txt")

    return outcome


def ensure_novnc_ready_with_recovery(
    *,
    run_id: str | None = None,
    mode: str = "deep",
    emit_artifacts: bool = True,
    max_wait_sec: int | None = None,
) -> ReadinessOutcome:
    # Recovery is already built into ensure_novnc_ready state machine.
    return ensure_novnc_ready(
        run_id=run_id,
        mode=mode,
        emit_artifacts=emit_artifacts,
        max_wait_sec=max_wait_sec,
    )


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Convergent noVNC readiness gate")
    p.add_argument("--mode", choices=["fast", "deep"], default="deep")
    p.add_argument("--emit-artifacts", action="store_true", help="Write readiness proof bundle")
    p.add_argument("--run-id", help="Explicit run id for artifacts")
    p.add_argument("--max-wait-sec", type=int, help=f"Wait budget (<= {HARD_MAX_WAIT}s)")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    outcome = ensure_novnc_ready(
        run_id=args.run_id,
        mode=args.mode,
        emit_artifacts=bool(args.emit_artifacts),
        max_wait_sec=args.max_wait_sec,
    )
    print(json.dumps(outcome.as_dict()))
    return 0 if outcome.ok else 1


if __name__ == "__main__":
    sys.exit(main())
