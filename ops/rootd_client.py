#!/usr/bin/env python3
"""
rootd_client — Client library for communicating with openclaw-rootd.

Used by hostd and ops scripts to request privileged operations.
Signs requests with HMAC. Fail-closed if rootd unreachable.

Usage:
    from ops.rootd_client import RootdClient
    client = RootdClient()
    result = client.exec("systemctl_restart", {"unit": "openclaw-hostd.service"})
    if not result["ok"]:
        print(result["reason"])
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import socket
from datetime import datetime, timezone


SOCKET_PATH = "/run/openclaw/rootd.sock"
HMAC_KEY_PATH = "/etc/ai-ops-runner/secrets/rootd_hmac_key"
CONNECT_TIMEOUT = 5
READ_TIMEOUT = 60


def _load_hmac_key() -> bytes | None:
    try:
        with open(HMAC_KEY_PATH, "rb") as f:
            key = f.read().strip()
            return key if len(key) >= 16 else None
    except OSError:
        return None


def _compute_hmac(payload: bytes) -> str:
    key = _load_hmac_key()
    if not key:
        return ""
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


class RootdClient:
    """Client for openclaw-rootd Unix socket API."""

    def __init__(self, socket_path: str | None = None, hmac_key_path: str | None = None):
        self._socket_path = socket_path or SOCKET_PATH
        self._hmac_key_path = hmac_key_path or HMAC_KEY_PATH

    def health(self) -> dict:
        """Check rootd health. Returns {"ok": bool, ...}."""
        try:
            return self._http_get("/health")
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def exec(self, command: str, args: dict | None = None, run_id: str | None = None) -> dict:
        """Execute a rootd command. Returns result dict with ok, reason, etc."""
        if not run_id:
            run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + os.urandom(4).hex()

        payload = {
            "command": command,
            "args": args or {},
            "run_id": run_id,
        }
        payload_bytes = json.dumps(payload).encode("utf-8")
        signature = _compute_hmac(payload_bytes)
        if not signature:
            return {
                "ok": False,
                "command": command,
                "run_id": run_id,
                "reason": f"Cannot read HMAC key from {self._hmac_key_path}. Denied (fail-closed).",
            }

        try:
            return self._http_post("/exec", payload_bytes, signature)
        except Exception as e:
            return {
                "ok": False,
                "command": command,
                "run_id": run_id,
                "reason": f"rootd unreachable at {self._socket_path}: {e}",
                "rootd_unavailable": True,
            }

    def is_available(self) -> bool:
        """Quick check if rootd socket exists and responds."""
        try:
            result = self.health()
            return result.get("ok", False)
        except Exception:
            return False

    def _http_get(self, path: str) -> dict:
        """Send HTTP GET over Unix socket."""
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(CONNECT_TIMEOUT)
        try:
            s.connect(self._socket_path)
            request = f"GET {path} HTTP/1.0\r\nHost: rootd\r\n\r\n"
            s.sendall(request.encode("utf-8"))
            return self._read_response(s)
        finally:
            s.close()

    def _http_post(self, path: str, body: bytes, hmac_sig: str) -> dict:
        """Send HTTP POST over Unix socket with HMAC header."""
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(CONNECT_TIMEOUT)
        try:
            s.connect(self._socket_path)
            s.settimeout(READ_TIMEOUT)
            headers = (
                f"POST {path} HTTP/1.0\r\n"
                f"Host: rootd\r\n"
                f"Content-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\n"
                f"X-RootD-HMAC: {hmac_sig}\r\n"
                f"\r\n"
            )
            s.sendall(headers.encode("utf-8") + body)
            return self._read_response(s)
        finally:
            s.close()

    def _read_response(self, s: socket.socket) -> dict:
        """Read full HTTP response and parse JSON body."""
        chunks = []
        while True:
            try:
                chunk = s.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
            except socket.timeout:
                break

        data = b"".join(chunks).decode("utf-8", errors="replace")
        if "\r\n\r\n" in data:
            body = data.split("\r\n\r\n", 1)[1]
        else:
            body = data

        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {"ok": False, "error": "Invalid JSON response from rootd", "raw": body[:500]}


def main() -> None:
    """CLI: check rootd health or exec a command."""
    import sys
    client = RootdClient()
    if len(sys.argv) < 2:
        print(json.dumps(client.health(), indent=2))
        return

    command = sys.argv[1]
    args = {}
    if len(sys.argv) > 2:
        try:
            args = json.loads(sys.argv[2])
        except json.JSONDecodeError:
            args = {"unit": sys.argv[2]}

    result = client.exec(command, args)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
