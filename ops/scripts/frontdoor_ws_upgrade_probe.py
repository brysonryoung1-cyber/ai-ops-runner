#!/usr/bin/env python3
"""Probe frontdoor websocket upgrade routing on localhost:8788.

Connects directly to the frontdoor TCP listener and sends a minimal
WebSocket upgrade request for each required path. PASS requires HTTP 101
for both /websockify and /novnc/websockify.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import socket
import sys
from pathlib import Path

DEFAULT_HOST = os.environ.get("OPENCLAW_FRONTDOOR_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("OPENCLAW_FRONTDOOR_PORT", "8788"))
DEFAULT_PATHS = ("/websockify", "/novnc/websockify")
READ_SIZE = 4096


def _slug(path: str) -> str:
    return path.strip("/").replace("/", "_") or "root"


def _ws_key() -> str:
    return base64.b64encode(secrets.token_bytes(16)).decode("ascii")


def _read_headers(sock: socket.socket) -> bytes:
    chunks: list[bytes] = []
    while True:
        chunk = sock.recv(READ_SIZE)
        if not chunk:
            break
        chunks.append(chunk)
        joined = b"".join(chunks)
        if b"\r\n\r\n" in joined:
            return joined.split(b"\r\n\r\n", 1)[0] + b"\r\n\r\n"
    return b"".join(chunks)


def _parse_headers(raw: bytes) -> tuple[str, int, dict[str, str]]:
    text = raw.decode("iso-8859-1", errors="replace")
    lines = text.split("\r\n")
    status_line = lines[0].strip() if lines else ""
    status_code = 0
    if status_line.startswith("HTTP/"):
        parts = status_line.split()
        if len(parts) >= 2 and parts[1].isdigit():
            status_code = int(parts[1])
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        headers[key.strip().lower()] = value.strip()
    return status_line, status_code, headers


def probe_endpoint(host: str, port: int, path: str, timeout: float) -> dict[str, object]:
    result: dict[str, object] = {
        "host": host,
        "port": port,
        "path": path,
        "ok": False,
        "status_line": "",
        "status_code": 0,
        "headers": {},
        "error": "",
        "raw_response": "",
    }
    raw_response = b""
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            request = (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {host}:{port}\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {_ws_key()}\r\n"
                "Sec-WebSocket-Version: 13\r\n"
                "Origin: http://127.0.0.1\r\n"
                "\r\n"
            )
            sock.sendall(request.encode("ascii"))
            raw_response = _read_headers(sock)
    except OSError as exc:
        result["error"] = f"connect_error:{type(exc).__name__}"
        return result

    status_line, status_code, headers = _parse_headers(raw_response)
    result["status_line"] = status_line
    result["status_code"] = status_code
    result["headers"] = {
        key: headers[key]
        for key in ("connection", "upgrade", "sec-websocket-accept", "server")
        if key in headers
    }
    result["raw_response"] = raw_response.decode("iso-8859-1", errors="replace")

    if status_code == 101:
        result["ok"] = True
        return result

    if status_code:
        result["error"] = f"http_{status_code}"
    else:
        result["error"] = "invalid_response"
    return result


def _build_summary(host: str, port: int, results: list[dict[str, object]]) -> dict[str, object]:
    first_failure = next((item for item in results if not item.get("ok")), None)
    message = "PASS"
    if first_failure:
        path = str(first_failure.get("path") or "")
        status_code = int(first_failure.get("status_code") or 0)
        if status_code:
            message = f"frontdoor_ws_upgrade_failed:{path}:HTTP_{status_code}"
        else:
            message = f"frontdoor_ws_upgrade_failed:{path}:{first_failure.get('error') or 'invalid_response'}"
    return {
        "host": host,
        "port": port,
        "all_ok": first_failure is None,
        "message": message,
        "results": results,
    }


def _write_artifacts(artifact_dir: Path, payload: dict[str, object]) -> None:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "result.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    lines = [payload.get("message", "")]
    for item in payload.get("results", []):
        path = str(item.get("path") or "")
        status_line = str(item.get("status_line") or item.get("error") or "")
        lines.append(f"{path} {status_line}".strip())
        slug = _slug(path)
        raw_response = str(item.get("raw_response") or "")
        (artifact_dir / f"{slug}_headers.txt").write_text(raw_response, encoding="utf-8")
    (artifact_dir / "summary.txt").write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe localhost frontdoor websocket upgrade paths")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Frontdoor host")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Frontdoor port")
    parser.add_argument("--path", action="append", dest="paths", help="Path to probe; repeatable")
    parser.add_argument("--timeout", type=float, default=5.0, help="Socket timeout in seconds")
    parser.add_argument("--artifact-dir", help="Optional artifact directory")
    args = parser.parse_args(argv)

    paths = args.paths or list(DEFAULT_PATHS)
    results = [probe_endpoint(args.host, args.port, path, args.timeout) for path in paths]
    payload = _build_summary(args.host, args.port, results)

    if args.artifact_dir:
        _write_artifacts(Path(args.artifact_dir), payload)

    print(json.dumps(payload, indent=2))
    return 0 if payload["all_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
