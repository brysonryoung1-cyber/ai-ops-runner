#!/usr/bin/env python3
"""noVNC WebSocket stability check — open WS to websockify, hold >= 10s, PASS only if stable.

Run on aiops-1. Uses stdlib only (socket, base64, struct, select).
Exit 0 + JSON on PASS. Exit 1 + JSON with close_code/close_reason on FAIL.
"""

from __future__ import annotations

import base64
import json
import os
import select
import socket
import struct
import sys
import time

HOLD_SEC = int(os.environ.get("OPENCLAW_WS_STABILITY_HOLD_SEC", "10"))
WS_PORT = int(os.environ.get("OPENCLAW_NOVNC_PORT", "6080"))
WS_HOST = "127.0.0.1"


def _gen_ws_key() -> str:
    return base64.b64encode(struct.pack("!I", int(time.time() * 1000) % (2**32))).decode()


def _send_upgrade(sock: socket.socket) -> bool:
    key = _gen_ws_key()
    req = (
        f"GET /websockify HTTP/1.1\r\n"
        f"Host: {WS_HOST}:{WS_PORT}\r\n"
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
    # opcode in first nibble
    if (data[0] & 0x0F) != 0x08:
        return 0, "not_close_frame"
    if len(data) >= 4:
        code = struct.unpack("!H", data[2:4])[0]
        reason = data[4:].decode("utf-8", errors="replace") if len(data) > 4 else ""
        return code, reason
    return 0, "no_payload"


def run_check() -> dict:
    result: dict = {
        "ok": False,
        "hold_sec": HOLD_SEC,
        "close_code": None,
        "close_reason": None,
        "elapsed_sec": None,
    }
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect((WS_HOST, WS_PORT))
        _send_upgrade(sock)
        resp = sock.recv(4096).decode("utf-8", errors="replace")
        if "101" not in resp and "Switching Protocols" not in resp:
            result["close_reason"] = f"upgrade_failed: {resp[:150]}"
            return result

        sock.setblocking(False)
        start = time.monotonic()
        while time.monotonic() - start < HOLD_SEC:
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
        result["elapsed_sec"] = HOLD_SEC
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


def main() -> int:
    result = run_check()
    print(json.dumps(result))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
