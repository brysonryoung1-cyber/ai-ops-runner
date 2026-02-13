#!/usr/bin/env python3
"""Manage OpenClaw Tailscale target profiles.

Targets file: ~/.config/openclaw/targets.json

Commands:
  init              — create default targets file if missing
  show              — display all targets and active selection
  set-active <name> — set the active target

Target schema:
  name      — human-readable label
  host      — must be in 100.64.0.0/10 (Tailscale CGNAT)
  user      — SSH user: "root" or "runner"
  repo_path — remote repo path (default: /opt/ai-ops-runner)

Security:
  - Hosts MUST be in the Tailscale CGNAT range (100.64.0.0/10).
  - Non-tailnet IPs are rejected fail-closed.
"""

import json
import os
import sys
from pathlib import Path

TARGETS_DIR = Path.home() / ".config" / "openclaw"
TARGETS_FILE = TARGETS_DIR / "targets.json"

DEFAULT_TARGETS = {
    "targets": {
        "aiops-1": {
            "name": "aiops-1",
            "host": "100.123.61.57",
            "user": "root",
            "repo_path": "/opt/ai-ops-runner",
        }
    },
    "active": "aiops-1",
}


def _err(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)


def is_tailscale_ip(ip: str) -> bool:
    """Validate that IP is in 100.64.0.0/10 (Tailscale CGNAT range)."""
    parts = ip.split(".")
    if len(parts) != 4:
        return False
    try:
        octets = [int(p) for p in parts]
    except ValueError:
        return False
    if any(o < 0 or o > 255 for o in octets):
        return False
    # 100.64.0.0/10 → first octet == 100, second octet 64–127
    return octets[0] == 100 and 64 <= octets[1] <= 127


def validate_target(target: dict) -> "str | None":
    """Validate a target dict. Returns error string or None if valid."""
    if not target.get("name"):
        return "Missing 'name' field"
    host = target.get("host", "")
    if not host:
        return "Missing 'host' field"
    if not is_tailscale_ip(host):
        return f"Host '{host}' is not in Tailscale CGNAT range (100.64.0.0/10)"
    user = target.get("user", "")
    if user and user not in ("root", "runner"):
        return f"User '{user}' must be 'root' or 'runner'"
    return None


def load_targets() -> dict:
    """Load targets file. Returns empty dict if missing or invalid."""
    if not TARGETS_FILE.exists():
        return {}
    try:
        with open(TARGETS_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        _err(f"Cannot read {TARGETS_FILE}: {exc}")
        return {}


def save_targets(data: dict) -> bool:
    """Write targets file."""
    try:
        TARGETS_DIR.mkdir(parents=True, exist_ok=True)
        with open(TARGETS_FILE, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        return True
    except OSError as exc:
        _err(f"Cannot write {TARGETS_FILE}: {exc}")
        return False


def get_active_target() -> "dict | None":
    """Return the active target dict, or None."""
    data = load_targets()
    active = data.get("active", "")
    targets = data.get("targets", {})
    if active and active in targets:
        return targets[active]
    return None


def cmd_init() -> int:
    """Create default targets file if missing."""
    if TARGETS_FILE.exists():
        print(f"Targets file already exists: {TARGETS_FILE}")
        return cmd_show()
    if save_targets(DEFAULT_TARGETS):
        print(f"Created {TARGETS_FILE} with default target.")
        print()
        return cmd_show()
    return 1


def cmd_show() -> int:
    """Display all targets and active selection."""
    data = load_targets()
    if not data:
        print(f"No targets configured. Run: python3 ops/openclaw_targets.py init")
        return 0

    active = data.get("active", "")
    targets = data.get("targets", {})

    print(f"Targets file: {TARGETS_FILE}")
    print(f"Active: {active or '(none)'}")
    print()

    for name, t in targets.items():
        marker = " *" if name == active else "  "
        host = t.get("host", "?")
        user = t.get("user", "root")
        repo = t.get("repo_path", "/opt/ai-ops-runner")
        print(f"{marker} {name}: {user}@{host}:{repo}")

    return 0


def cmd_set_active(name: str) -> int:
    """Set the active target by name."""
    data = load_targets()
    targets = data.get("targets", {})

    if name not in targets:
        available = ", ".join(targets.keys()) if targets else "(none)"
        _err(f"Target '{name}' not found. Available: {available}")
        return 1

    err = validate_target(targets[name])
    if err:
        _err(f"Target '{name}' is invalid: {err}")
        return 1

    data["active"] = name
    if save_targets(data):
        print(f"Active target set to: {name}")
        return 0
    return 1


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: openclaw_targets.py <init|show|set-active <name>>")
        return 1

    cmd = sys.argv[1]
    if cmd == "init":
        return cmd_init()
    elif cmd == "show":
        return cmd_show()
    elif cmd == "set-active":
        if len(sys.argv) < 3:
            _err("Usage: openclaw_targets.py set-active <name>")
            return 1
        return cmd_set_active(sys.argv[2])
    else:
        _err(f"Unknown command: {cmd}")
        print("Usage: openclaw_targets.py <init|show|set-active <name>>")
        return 1


if __name__ == "__main__":
    sys.exit(main())
