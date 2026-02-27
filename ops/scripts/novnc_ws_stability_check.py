#!/usr/bin/env python3
"""noVNC WebSocket stability check — open WS to websockify, hold >= 10s, PASS only if stable.

Run on aiops-1. Uses stdlib only (socket, base64, struct, select).
Supports: --local (127.0.0.1), --tailnet <host>, or --all (both).
Exit 0 + JSON on PASS. Exit 1 + JSON with close_code/close_reason on FAIL.
"""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import os
import select
import socket
import struct
import sys
import time

HOLD_SEC = int(os.environ.get("OPENCLAW_WS_STABILITY_HOLD_SEC", "10"))
WS_PORT = int(os.environ.get("OPENCLAW_NOVNC_PORT", "6080"))
LOCAL_HOST = "127.0.0.1"


def _gen_ws_key() -> str:
    return base64.b64encode(struct.pack("!I", int(time.time() * 1000) % (2**32))).decode()


def _send_upgrade(sock: socket.socket, host: str, port: int) -> bool:
    key = _gen_ws_key()
    req = (
        f"GET /websockify HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        f"Upgrade: websocket\r\n"
        f"Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        f"Sec-WebSocket-Version: 13\r\n"
        f"\r\n"
    )
    sock.sendall(req.encode())
    return True


def _parse_close_frame(data: bytes) -> tuple[int, str]:
    """Parse WebSocket close frame; return (code, reason)."""
    if len(data) < 2:
        return 0, "truncated"
    if (data[0] & 0x0F) != 0x08:
        return 0, "not_close_frame"
    if len(data) >= 4:
        code = struct.unpack("!H", data[2:4])[0]
        reason = data[4:].decode("utf-8", errors="replace") if len(data) > 4 else ""
        return code, reason
    return 0, "no_payload"


def run_check(host: str, port: int, hold_sec: int = HOLD_SEC) -> dict:
    """Run WS stability check against host:port. Return result dict."""
    result: dict = {
        "ok": False,
        "host": host,
        "port": port,
        "hold_sec": hold_sec,
        "close_code": None,
        "close_reason": None,
        "elapsed_sec": None,
    }
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect((host, port))
        _send_upgrade(sock, host, port)
        resp = sock.recv(4096).decode("utf-8", errors="replace")
        if "101" not in resp and "Switching Protocols" not in resp:
            result["close_reason"] = f"upgrade_failed: {resp[:150]}"
            return result

        sock.setblocking(False)
        start = time.monotonic()
        while time.monotonic() - start < hold_sec:
            r, _, _ = select.select([sock], [], [], 1.0)
            if r:
                data = sock.recv(4096)
                if not data:
                    result["elapsed_sec"] = round(time.monotonic() - start, 1)
                    result["close_reason"] = "connection_closed_early"
                    return result
                if len(data) >= 1 and (data[0] & 0x0F) == 0x08:
                    code, reason = _parse_close_frame(data)
                    result["elapsed_sec"] = round(time.monotonic() - start, 1)
                    result["close_code"] = code
                    result["close_reason"] = reason or f"code_{code}"
                    return result

        result["ok"] = True
        result["elapsed_sec"] = hold_sec
        return result
    except (socket.timeout, ConnectionRefusedError, OSError) as e:
        result["close_reason"] = str(e)[:120]
        return result
    finally:
        if sock:
            try:
                sock.close()
            except OSError:
                pass

    return result


def _get_tailnet_host() -> str:
    """Get Tailscale DNSName from tailscale status --json."""
    try:
        out = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if out.returncode == 0:
            data = json.loads(out.stdout)
            name = (data.get("Self") or {}).get("DNSName", "").rstrip(".")
            if name and ".ts.net" in name:
                return name
    except Exception:
        pass
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description="noVNC WebSocket stability check")
    parser.add_argument("--local", action="store_true", help="Check 127.0.0.1 only")
    parser.add_argument("--tailnet", action="store_true", help="Check tailnet host only")
    parser.add_argument("--all", action="store_true", help="Check both local and tailnet (default)")
    parser.add_argument("--host", type=str, help="Specific host to check (overrides --local/--tailnet)")
    parser.add_argument("--port", type=int, default=WS_PORT, help=f"Port (default {WS_PORT})")
    args = parser.parse_args()

    port = args.port
    hold_sec = HOLD_SEC

    if args.host:
        result = run_check(args.host, port, hold_sec)
        print(json.dumps(result))
        return 0 if result.get("ok") else 1

    if args.local:
        result = run_check(LOCAL_HOST, port, hold_sec)
        print(json.dumps(result))
        return 0 if result.get("ok") else 1

    if args.tailnet:
        host = _get_tailnet_host()
        if not host:
            out = {"ok": False, "close_reason": "tailscale_dns_unavailable", "host": ""}
            print(json.dumps(out))
            return 1
        result = run_check(host, port, hold_sec)
        print(json.dumps(result))
        return 0 if result.get("ok") else 1

    # --all (default): run both local and tailnet
    local_result = run_check(LOCAL_HOST, port, hold_sec)
    tailnet_host = _get_tailnet_host()
    tailnet_result: dict
    if tailnet_host:
        tailnet_result = run_check(tailnet_host, port, hold_sec)
    else:
        tailnet_result = {"ok": False, "host": "", "close_reason": "tailscale_dns_unavailable"}

    combined = {
        "local": local_result,
        "tailnet": tailnet_result,
        "ws_stability_local": "verified" if local_result.get("ok") else "failed",
        "ws_stability_tailnet": "verified" if tailnet_result.get("ok") else "failed",
        "ok": local_result.get("ok") and tailnet_result.get("ok"),
    }
    print(json.dumps(combined))
    return 0 if combined["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
