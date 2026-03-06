#!/usr/bin/env python3
"""Deterministic backend VNC TCP probe for noVNC readiness/doctor checks."""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_HOST = os.environ.get("OPENCLAW_NOVNC_VNC_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("OPENCLAW_NOVNC_VNC_PORT", os.environ.get("VNC_PORT", "5900")))
DEFAULT_TIMEOUT_SEC = float(os.environ.get("OPENCLAW_NOVNC_VNC_TIMEOUT_SEC", "1.0"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def probe_backend_vnc(host: str, port: int, timeout_sec: float) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ok": False,
        "host": host,
        "port": int(port),
        "timeout_sec": float(timeout_sec),
        "error": None,
        "ts": _now_iso(),
    }
    try:
        with socket.create_connection((host, int(port)), timeout=float(timeout_sec)):
            result["ok"] = True
    except OSError as exc:
        result["error"] = str(exc)[:240]
    return result


def _write_artifacts(artifact_dir: Path, payload: dict[str, Any]) -> None:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    status = {
        "status": "PASS" if payload.get("ok") else "FAIL",
        "ok": bool(payload.get("ok")),
        "host": payload.get("host"),
        "port": payload.get("port"),
        "timeout_sec": payload.get("timeout_sec"),
        "error": payload.get("error"),
        "ts": payload.get("ts"),
    }
    (artifact_dir / "status.json").write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
    (artifact_dir / "details.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Probe noVNC backend VNC TCP socket")
    p.add_argument("--host", default=DEFAULT_HOST, help="Backend VNC host")
    p.add_argument("--port", type=int, default=DEFAULT_PORT, help="Backend VNC port")
    p.add_argument("--timeout-sec", type=float, default=DEFAULT_TIMEOUT_SEC, help="TCP connect timeout in seconds")
    p.add_argument("--artifact-dir", help="Optional artifact output directory")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    payload = probe_backend_vnc(args.host, args.port, args.timeout_sec)
    if args.artifact_dir:
        _write_artifacts(Path(args.artifact_dir), payload)
    print(json.dumps(payload, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
