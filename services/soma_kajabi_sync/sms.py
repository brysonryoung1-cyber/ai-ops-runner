#!/usr/bin/env python3
"""SMS integration for OpenClaw — Twilio-based outbound alerts + inbound commands.

Outbound alerts for:
  - Workflow SUCCESS/FAIL
  - Doctor FAIL
  - Guard FAIL
  - Nightly FAIL
  - SIZE_CAP WARN

Inbound SMS commands (from allowlisted phone numbers):
  - STATUS      → Current system status summary
  - RUN_SNAPSHOT → Trigger Kajabi snapshot
  - RUN_HARVEST  → Trigger Gmail harvest
  - RUN_MIRROR   → Trigger mirror operation
  - LAST_ERRORS  → Last 5 error messages

All behind allowlist + token, tailnet-only, fail-closed.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .config import load_secret, mask_secret

# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------
_RATE_DIR = Path(os.environ.get("SMS_RATE_DIR", "/tmp/openclaw_sms_ratelimit"))
_OUTBOUND_RATE_LIMIT_SEC = int(os.environ.get("SMS_OUTBOUND_RATE_LIMIT_SEC", "1800"))
_INBOUND_RATE_LIMIT_SEC = int(os.environ.get("SMS_INBOUND_RATE_LIMIT_SEC", "60"))


def _sha256(data: str) -> str:
    return hashlib.sha256(data.encode()).hexdigest()


def _check_rate_limit(key: str, limit_sec: int) -> bool:
    """Return True if the action is allowed (not rate-limited)."""
    _RATE_DIR.mkdir(parents=True, exist_ok=True)
    stamp_file = _RATE_DIR / _sha256(key)
    if stamp_file.exists():
        try:
            last_sent = float(stamp_file.read_text().strip())
            elapsed = time.time() - last_sent
            if elapsed < limit_sec:
                return False
        except (ValueError, OSError):
            pass
    return True


def _mark_rate_sent(key: str) -> None:
    """Mark a rate-limit key as sent NOW."""
    _RATE_DIR.mkdir(parents=True, exist_ok=True)
    stamp_file = _RATE_DIR / _sha256(key)
    stamp_file.write_text(str(time.time()))


# ---------------------------------------------------------------------------
# Allowlist
# ---------------------------------------------------------------------------
def _load_allowlist() -> set[str]:
    """Load SMS allowlist (comma-separated phone numbers).
    Normalizes to E.164-ish format (digits only, leading +).
    """
    raw = load_secret("SMS_ALLOWLIST", required=False)
    if not raw:
        return set()
    numbers: set[str] = set()
    for num in raw.split(","):
        cleaned = num.strip().replace("-", "").replace("(", "").replace(")", "").replace(" ", "")
        if cleaned:
            if not cleaned.startswith("+"):
                cleaned = "+1" + cleaned  # Default to US
            numbers.add(cleaned)
    return numbers


def is_allowed_sender(phone: str) -> bool:
    """Check if a phone number is in the SMS allowlist."""
    allowlist = _load_allowlist()
    if not allowlist:
        return False  # Fail-closed: empty allowlist = deny all
    # Normalize incoming number
    cleaned = phone.strip().replace("-", "").replace("(", "").replace(")", "").replace(" ", "")
    if not cleaned.startswith("+"):
        cleaned = "+1" + cleaned
    return cleaned in allowlist


# ---------------------------------------------------------------------------
# Outbound SMS
# ---------------------------------------------------------------------------
def send_sms(
    to: str,
    message: str,
    rate_key: Optional[str] = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Send an SMS via Twilio.

    Secrets sent via POST body (not in process args).
    Rate-limited by rate_key (default: 30 min).
    """
    if rate_key and not _check_rate_limit(f"sms_out_{rate_key}", _OUTBOUND_RATE_LIMIT_SEC):
        return {"ok": False, "reason": "rate_limited", "rate_key": rate_key}

    if dry_run:
        return {"ok": True, "reason": "dry_run", "to": to, "message": message}

    account_sid = load_secret("TWILIO_ACCOUNT_SID")
    auth_token = load_secret("TWILIO_AUTH_TOKEN")
    from_number = load_secret("TWILIO_FROM_NUMBER")
    assert account_sid and auth_token and from_number

    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"

    data = urllib.parse.urlencode({
        "To": to,
        "From": from_number,
        "Body": message[:1600],  # Twilio max
    }).encode()

    # Basic auth via urllib — secrets never in process argv
    import base64
    auth_header = base64.b64encode(f"{account_sid}:{auth_token}".encode()).decode()

    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", f"Basic {auth_header}")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode())
            sid = result.get("sid", "unknown")
    except Exception as e:
        return {"ok": False, "reason": "send_failed", "error": str(e)}

    if rate_key:
        _mark_rate_sent(f"sms_out_{rate_key}")

    return {"ok": True, "sid": sid, "to": to}


def send_alert(
    event: str,
    message: str,
    rate_key: Optional[str] = None,
) -> dict[str, Any]:
    """Send an alert SMS to all numbers in the allowlist.

    Rate key is per-recipient to avoid fan-out suppression: if we send to
    3 numbers with the same rate_key, each gets its own rate bucket so
    all 3 receive the alert (not just the first).
    """
    allowlist = _load_allowlist()
    if not allowlist:
        return {"ok": False, "reason": "no_allowlist"}

    results = []
    for number in allowlist:
        # Per-recipient rate key to prevent fan-out suppression
        per_recipient_key = f"{rate_key}_{number}" if rate_key else None
        r = send_sms(number, f"[OpenClaw] {event}: {message}", rate_key=per_recipient_key)
        results.append(r)

    return {
        "ok": all(r["ok"] for r in results),
        "sent_to": len(results),
        "results": results,
    }


# ---------------------------------------------------------------------------
# Inbound SMS command handler
# ---------------------------------------------------------------------------
_INBOUND_COMMANDS = {
    "STATUS", "RUN_SNAPSHOT", "RUN_HARVEST", "RUN_MIRROR", "LAST_ERRORS"
}

# Error log for LAST_ERRORS command
_ERROR_LOG_PATH = Path(os.environ.get(
    "SMS_ERROR_LOG", "/tmp/openclaw_sms_errors.jsonl"
))


def log_error(message: str) -> None:
    """Append an error to the SMS error log."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "message": message,
    }
    with open(_ERROR_LOG_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")


def get_last_errors(n: int = 5) -> list[dict[str, str]]:
    """Get the last N errors from the log."""
    if not _ERROR_LOG_PATH.exists():
        return []
    lines = _ERROR_LOG_PATH.read_text().strip().split("\n")
    errors = []
    for line in lines[-n:]:
        try:
            errors.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return errors


def handle_inbound_sms(
    from_number: str,
    body: str,
) -> dict[str, Any]:
    """Handle an inbound SMS command.

    Validates sender against allowlist, rate-limits per sender.
    Returns response dict.
    """
    # Allowlist check (fail-closed)
    if not is_allowed_sender(from_number):
        return {
            "ok": False,
            "reason": "not_allowed",
            "from": mask_secret(from_number),
        }

    # Rate limit per sender
    if not _check_rate_limit(f"sms_in_{from_number}", _INBOUND_RATE_LIMIT_SEC):
        return {
            "ok": False,
            "reason": "rate_limited",
            "from": mask_secret(from_number),
        }
    _mark_rate_sent(f"sms_in_{from_number}")

    # Parse command
    command = body.strip().upper().replace(" ", "_")
    if command not in _INBOUND_COMMANDS:
        reply = (
            f"Unknown command: {body.strip()}\n"
            f"Available: {', '.join(sorted(_INBOUND_COMMANDS))}"
        )
        send_sms(from_number, reply)
        return {"ok": False, "reason": "unknown_command", "command": body.strip()}

    # Execute command
    if command == "STATUS":
        reply = _cmd_status()
    elif command == "RUN_SNAPSHOT":
        reply = _cmd_run_snapshot()
    elif command == "RUN_HARVEST":
        reply = _cmd_run_harvest()
    elif command == "RUN_MIRROR":
        reply = _cmd_run_mirror()
    elif command == "LAST_ERRORS":
        reply = _cmd_last_errors()
    else:
        reply = "Internal error: unhandled command"

    send_sms(from_number, reply)
    return {"ok": True, "command": command, "from": mask_secret(from_number)}


def _cmd_status() -> str:
    """STATUS command: return system status summary."""
    from .config import ARTIFACTS_ROOT

    # Check for recent artifacts
    soma_dirs = sorted(
        (d for d in ARTIFACTS_ROOT.iterdir() if d.is_dir()),
        key=lambda x: x.stat().st_mtime,
        reverse=True,
    ) if ARTIFACTS_ROOT.exists() else []

    last_run = soma_dirs[0].name if soma_dirs else "none"
    total_runs = len(soma_dirs)

    return (
        f"OpenClaw Soma Status\n"
        f"Total runs: {total_runs}\n"
        f"Last run: {last_run}\n"
        f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    )


def _cmd_run_snapshot() -> str:
    """RUN_SNAPSHOT command: trigger Kajabi snapshot (Home)."""
    try:
        from .snapshot import snapshot_kajabi
        result = snapshot_kajabi("Home User Library")
        return f"Snapshot complete: {result['total_categories']} categories, {result['total_items']} items"
    except Exception as e:
        log_error(f"RUN_SNAPSHOT failed: {e}")
        return f"Snapshot FAILED: {e}"


def _cmd_run_harvest() -> str:
    """RUN_HARVEST command: trigger Gmail harvest."""
    try:
        from .harvest import harvest_gmail
        result = harvest_gmail()
        return f"Harvest complete: {result['total_emails']} emails, {result['total_videos']} videos"
    except Exception as e:
        log_error(f"RUN_HARVEST failed: {e}")
        return f"Harvest FAILED: {e}"


def _cmd_run_mirror() -> str:
    """RUN_MIRROR command: trigger mirror operation."""
    try:
        from .mirror import mirror_home_to_practitioner
        result = mirror_home_to_practitioner()
        return f"Mirror complete: {result['summary']['total_actions']} actions"
    except Exception as e:
        log_error(f"RUN_MIRROR failed: {e}")
        return f"Mirror FAILED: {e}"


def _cmd_last_errors() -> str:
    """LAST_ERRORS command: return last 5 errors."""
    errors = get_last_errors(5)
    if not errors:
        return "No recent errors."
    lines = ["Last errors:"]
    for e in errors:
        ts = e.get("timestamp", "?")[:16]
        msg = e.get("message", "?")[:80]
        lines.append(f"  {ts}: {msg}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI for testing
# ---------------------------------------------------------------------------
def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="OpenClaw SMS integration")
    sub = parser.add_subparsers(dest="cmd")

    # send
    send_p = sub.add_parser("send", help="Send an SMS")
    send_p.add_argument("--to", required=True)
    send_p.add_argument("--message", required=True)
    send_p.add_argument("--dry-run", action="store_true")

    # alert
    alert_p = sub.add_parser("alert", help="Send alert to all allowlisted numbers")
    alert_p.add_argument("--event", required=True)
    alert_p.add_argument("--message", required=True)
    alert_p.add_argument("--rate-key")

    # test
    sub.add_parser("test", help="Test SMS configuration")

    # status
    sub.add_parser("status", help="Show SMS config status")

    args = parser.parse_args()

    if args.cmd == "send":
        r = send_sms(args.to, args.message, dry_run=args.dry_run)
        print(json.dumps(r, indent=2))
    elif args.cmd == "alert":
        r = send_alert(args.event, args.message, rate_key=args.rate_key)
        print(json.dumps(r, indent=2))
    elif args.cmd == "test":
        # Validate configuration
        print("=== SMS Configuration Test ===")
        for name in ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER", "SMS_ALLOWLIST"]:
            val = load_secret(name, required=False)
            if val:
                print(f"  {name}: found ({mask_secret(val)})")
            else:
                print(f"  {name}: NOT FOUND")
        allowlist = _load_allowlist()
        print(f"  Allowlist size: {len(allowlist)}")
        print(f"  Rate limit (outbound): {_OUTBOUND_RATE_LIMIT_SEC}s")
        print(f"  Rate limit (inbound): {_INBOUND_RATE_LIMIT_SEC}s")
    elif args.cmd == "status":
        print(_cmd_status())
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
