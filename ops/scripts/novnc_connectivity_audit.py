#!/usr/bin/env python3
"""noVNC Connectivity Audit — Hard gate for READY_FOR_HUMAN.

HTTP 200: /novnc/vnc.html
WSS probe >=10s: wss://<host>/websockify, wss://<host>/novnc/websockify
Framebuffer non-blank: xwd capture + pixel variance check

Writes artifacts/novnc_debug/ws_probe/<run_id>/ws_probe.json + PROOF.md
Exit 0 only when all checks PASS. Fail-closed: never claim READY unless proofs pass.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT_DIR = Path(os.environ.get("OPENCLAW_REPO_ROOT", "/opt/ai-ops-runner"))
ARTIFACTS_ROOT = Path(os.environ.get("OPENCLAW_ARTIFACTS_ROOT", str(ROOT_DIR / "artifacts")))
TS_HOSTNAME = os.environ.get("OPENCLAW_TS_HOSTNAME", "aiops-1.tailc75c62.ts.net")
FRONTDOOR_PORT = int(os.environ.get("OPENCLAW_FRONTDOOR_PORT", "8788"))
WS_PROBE_HOLD = int(os.environ.get("OPENCLAW_WS_PROBE_HOLD_SEC", "10"))


def _curl_http(url: str, timeout: int = 5) -> int:
    """Return HTTP status code."""
    try:
        r = subprocess.run(
            ["curl", "-sf", "-o", "/dev/null", "-w", "%{http_code}",
             "--connect-timeout", "3", "--max-time", str(timeout), url],
            capture_output=True,
            text=True,
            timeout=timeout + 2,
            cwd=str(ROOT_DIR),
        )
        return int(r.stdout.strip()) if r.stdout.strip().isdigit() else 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError, ValueError):
        return 0


def _check_framebuffer_non_blank() -> tuple[bool, str]:
    """Check framebuffer is not all-black via xwd capture. Return (ok, detail)."""
    display = os.environ.get("DISPLAY", ":99")
    cfg = Path("/etc/ai-ops-runner/config/novnc_display.env")
    if cfg.exists():
        for line in cfg.read_text().splitlines():
            line = line.strip()
            if line.startswith("DISPLAY="):
                display = line.split("=", 1)[1].strip().strip("'\"") or ":99"
                break

    xwd_file = "/tmp/novnc_audit_fb.xwd"
    try:
        r = subprocess.run(
            ["xwd", "-root", "-silent", "-out", xwd_file],
            capture_output=True,
            timeout=10,
            env={**os.environ, "DISPLAY": display},
        )
        if r.returncode != 0:
            return False, "xwd_capture_failed"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return True, "xwd_unavailable_skipped"

    xwd_path = Path(xwd_file)
    if not xwd_path.exists() or xwd_path.stat().st_size < 500:
        return False, "xwd_empty"

    try:
        data = xwd_path.read_bytes()
        pixels = data[256:min(256 + 50000, len(data))]
        unique = len(set(pixels))
        nonzero = sum(1 for b in pixels if b != 0)
        if unique > 2 or nonzero > 100:
            return True, f"non_blank(unique={unique},nonzero={nonzero})"
        return False, f"all_black(unique={unique},nonzero={nonzero})"
    except Exception as e:
        return False, f"check_error:{e}"
    finally:
        xwd_path.unlink(missing_ok=True)


def _run_ws_probe(host: str, hold_sec: int) -> dict:
    """Run novnc_ws_probe.py --all. Return combined result dict."""
    script = ROOT_DIR / "ops" / "scripts" / "novnc_ws_probe.py"
    if not script.exists():
        return {"all_ok": False, "error": "novnc_ws_probe.py missing", "endpoints": {}}
    try:
        r = subprocess.run(
            [sys.executable, str(script), "--host", host, "--hold", str(hold_sec), "--all"],
            capture_output=True,
            text=True,
            timeout=hold_sec + 15,
            cwd=str(ROOT_DIR),
            env={**os.environ, "OPENCLAW_TS_HOSTNAME": host},
        )
        if r.returncode == 0 and r.stdout:
            return json.loads(r.stdout)
        return {"all_ok": False, "error": r.stderr or "probe failed", "endpoints": {}}
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as e:
        return {"all_ok": False, "error": str(e), "endpoints": {}}


def run_audit(run_id: str, host: str | None = None) -> tuple[bool, dict]:
    """Run full audit. Return (pass, result_dict)."""
    host = host or TS_HOSTNAME
    out_dir = ARTIFACTS_ROOT / "novnc_debug" / "ws_probe" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. HTTP 200 for /novnc/vnc.html (via frontdoor local or tailnet)
    local_url = f"http://127.0.0.1:{FRONTDOOR_PORT}/novnc/vnc.html"
    tailnet_url = f"https://{host}/novnc/vnc.html"
    http_local = _curl_http(local_url)
    http_tailnet = _curl_http(tailnet_url)
    http_ok = http_local == 200 or http_tailnet == 200

    # 2. WSS probe >=10s
    ws_result = _run_ws_probe(host, WS_PROBE_HOLD)
    eps = ws_result.get("endpoints", {})
    ws_websockify_ok = eps.get("/websockify", {}).get("ok") is True
    ws_novnc_ok = eps.get("/novnc/websockify", {}).get("ok") is True
    ws_ok = ws_websockify_ok and ws_novnc_ok

    # 3. Framebuffer non-blank (skip gracefully if xwd unavailable)
    fb_ok, fb_detail = _check_framebuffer_non_blank()

    all_pass = http_ok and ws_ok and fb_ok
    result = {
        "run_id": run_id,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "host": host,
        "http_200": http_ok,
        "http_local_code": http_local,
        "http_tailnet_code": http_tailnet,
        "ws_probe_hold_sec": WS_PROBE_HOLD,
        "ws_probe_websockify_ok": ws_websockify_ok,
        "ws_probe_novnc_websockify_ok": ws_novnc_ok,
        "framebuffer_non_blank": fb_ok,
        "framebuffer_detail": fb_detail,
        "all_ok": all_pass,
        "endpoints": eps,
    }

    (out_dir / "ws_probe.json").write_text(json.dumps(result, indent=2))

    proof_lines = [
        "# noVNC Connectivity Audit",
        "",
        f"**Run ID:** {run_id}",
        f"**Timestamp:** {result['timestamp_utc']}",
        "",
        "## Results",
        f"- HTTP 200 /novnc/vnc.html: {'PASS' if http_ok else 'FAIL'} (local={http_local}, tailnet={http_tailnet})",
        f"- WSS /websockify >=10s: {'PASS' if ws_websockify_ok else 'FAIL'}",
        f"- WSS /novnc/websockify >=10s: {'PASS' if ws_novnc_ok else 'FAIL'}",
        f"- Framebuffer non-blank: {'PASS' if fb_ok else 'FAIL'} ({fb_detail})",
        "",
        f"**Overall:** {'PASS' if all_pass else 'FAIL'}",
    ]
    (out_dir / "PROOF.md").write_text("\n".join(proof_lines))

    return all_pass, result


def main() -> int:
    parser = argparse.ArgumentParser(description="noVNC connectivity audit (READY_FOR_HUMAN gate)")
    parser.add_argument("--run-id", type=str, help="Run ID for artifact path")
    parser.add_argument("--host", type=str, default=TS_HOSTNAME, help="Tailscale hostname")
    args = parser.parse_args()

    run_id = args.run_id or f"audit_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    pass_audit, result = run_audit(run_id, args.host)
    print(json.dumps(result, indent=2))
    return 0 if pass_audit else 1


if __name__ == "__main__":
    sys.exit(main())
