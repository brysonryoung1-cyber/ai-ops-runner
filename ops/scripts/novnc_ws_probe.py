#!/usr/bin/env python3
"""noVNC WebSocket probe — WSS over 443 (same path as browser).

Probes wss://<host>/websockify and wss://<host>/novnc/websockify.
Completes TLS handshake + websocket upgrade, holds >= 10s.
Reports close_code/close_reason if server closes early.

Use for forensic bundle and frontdoor verification. Stdlib only (ssl, socket).
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import select
import socket
import ssl
import struct
import sys
import time

HOLD_SEC = int(os.environ.get("OPENCLAW_WS_PROBE_HOLD_SEC", "10"))
DEFAULT_HOST = os.environ.get("OPENCLAW_TS_HOSTNAME", "aiops-1.tailc75c62.ts.net")
WSS_PORT = 443


def _gen_ws_key() -> str:
    return base64.b64encode(struct.pack("!I", int(time.time() * 1000) % (2**32))).decode()


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


def probe_wss(
    host: str,
    path: str,
    port: int = WSS_PORT,
    hold_sec: int = HOLD_SEC,
) -> dict:
    """Probe WSS endpoint. Return result dict."""
    result: dict = {
        "ok": False,
        "host": host,
        "path": path,
        "port": port,
        "hold_sec": hold_sec,
        "close_code": None,
        "close_reason": None,
        "exception": None,
        "elapsed_sec": None,
    }
    sock = None
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        # Force HTTP/1.1 ALPN so server doesn't negotiate h2 (WebSocket needs HTTP/1.1)
        if hasattr(ctx, "set_alpn_protocols"):
            ctx.set_alpn_protocols(["http/1.1"])
        raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        raw.settimeout(10)
        sock = ctx.wrap_socket(raw, server_hostname=host)
        sock.connect((host, port))
        key = _gen_ws_key()
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n"
            f"\r\n"
        )
        sock.sendall(req.encode())
        resp = sock.recv(4096).decode("utf-8", errors="replace")
        if "101" not in resp and "Switching Protocols" not in resp:
            result["close_reason"] = f"upgrade_failed: {resp[:200]}"
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
    except (socket.timeout, ssl.SSLError, ConnectionRefusedError, OSError) as e:
        result["close_reason"] = str(e)[:150]
        result["exception"] = type(e).__name__
        return result
    finally:
        if sock:
            try:
                sock.close()
            except OSError:
                pass

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="noVNC WSS probe (443, browser path)")
    parser.add_argument("--host", type=str, default=DEFAULT_HOST, help="Tailscale hostname")
    parser.add_argument("--hold", type=int, default=HOLD_SEC, help="Hold seconds")
    parser.add_argument("--path", type=str, help="Single path (e.g. /websockify)")
    parser.add_argument("--all", action="store_true", help="Probe /websockify and /novnc/websockify")
    args = parser.parse_args()

    paths = []
    if args.path:
        paths = [args.path]
    elif args.all:
        paths = ["/websockify", "/novnc/websockify"]
    else:
        paths = ["/websockify", "/novnc/websockify"]

    results: dict[str, dict] = {}
    for p in paths:
        results[p] = probe_wss(args.host, p, WSS_PORT, args.hold)

    combined = {
        "host": args.host,
        "hold_sec": args.hold,
        "endpoints": results,
        "all_ok": all(r.get("ok") for r in results.values()),
    }
    print(json.dumps(combined, indent=2))
    return 0 if combined["all_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
