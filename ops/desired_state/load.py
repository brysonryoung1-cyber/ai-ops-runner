#!/usr/bin/env python3
"""
Load and validate OpenClaw desired state.
Fail-closed: raises on invalid/missing state.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT_DIR = Path(os.environ.get("OPENCLAW_REPO_ROOT", "/opt/ai-ops-runner"))
DESIRED_STATE_PATH = ROOT_DIR / "ops" / "desired_state" / "openclaw_desired_state.json"
SCHEMA_PATH = ROOT_DIR / "ops" / "desired_state" / "openclaw_desired_state.schema.json"


def load_desired_state(path: Path | None = None) -> dict:
    """Load desired state JSON. Raises on missing/invalid."""
    p = path or DESIRED_STATE_PATH
    if not p.exists():
        raise FileNotFoundError(f"Desired state not found: {p}")
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    return validate_desired_state(data)


def validate_desired_state(data: dict) -> dict:
    """Validate required fields. Raises ValueError on invalid."""
    required = ["version", "tailscale_serve", "frontdoor", "ports_services", "novnc", "invariants"]
    for key in required:
        if key not in data:
            raise ValueError(f"Desired state missing required key: {key}")

    ts = data["tailscale_serve"]
    if not isinstance(ts.get("single_root"), bool):
        raise ValueError("tailscale_serve.single_root must be boolean")
    if not ts.get("target") or "127.0.0.1:8788" not in str(ts.get("target", "")):
        raise ValueError("tailscale_serve.target must target 127.0.0.1:8788")

    fd = data["frontdoor"]
    if "8788" not in str(fd.get("listen", "")):
        raise ValueError("frontdoor.listen must include 8788")

    novnc = data["novnc"]
    if "/novnc/vnc.html" not in str(novnc.get("http_path", "")):
        raise ValueError("novnc.http_path must be /novnc/vnc.html")
    ws_paths = novnc.get("ws_paths") or []
    if "/websockify" not in ws_paths or "/novnc/websockify" not in ws_paths:
        raise ValueError("novnc.ws_paths must include /websockify and /novnc/websockify")

    return data


def get_canonical_novnc_url(host: str) -> str:
    """Return canonical noVNC URL for host."""
    state = load_desired_state()
    fmt = state.get("novnc", {}).get("canonical_url_format", "")
    return fmt.replace("<host>", host.replace("https://", "").replace("http://", "").split("/")[0].split(":")[0])
