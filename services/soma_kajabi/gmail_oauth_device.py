"""Gmail OAuth 2.0 device flow (RFC 8628). No password storage.

Requires client_id/client_secret in secrets/soma_kajabi/gmail_client.json
(Google Cloud OAuth "Desktop" or "Limited Input Device" app).
"""

from __future__ import annotations

import json
import time
import urllib.parse
from pathlib import Path
from typing import Any

try:
    import urllib.request
    import urllib.error
    _HAS_URLLIB = True
except ImportError:
    _HAS_URLLIB = False

from .connector_config import GMAIL_OAUTH_PATH, GMAIL_STATE_PATH, SOMA_KAJABI_SECRETS, _repo_root

GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
DEVICE_CODE_URL = "https://oauth2.googleapis.com/device/code"
TOKEN_URL = "https://oauth2.googleapis.com/token"


def _load_client(root: Path) -> tuple[str, str] | None:
    """Load client_id and client_secret. Returns (client_id, client_secret) or None."""
    # Prefer per-project client file, then soma_kajabi secrets
    for base in (root / "config" / "secrets", SOMA_KAJABI_SECRETS):
        path = base / "gmail_client.json"
        if path.exists():
            try:
                data = json.loads(path.read_text())
                if isinstance(data, dict):
                    cid = data.get("client_id") or (data.get("installed") or {}).get("client_id")
                    csec = data.get("client_secret") or (data.get("installed") or {}).get("client_secret")
                    if cid and csec:
                        return (cid, csec)
            except Exception:
                pass
    return None


def _mask_fingerprint(s: str) -> str:
    if not s or len(s) < 8:
        return "***"
    return f"{s[:4]}...{s[-4:]}"


REQUIREMENTS_ENDPOINT_PATH = "/api/connectors/gmail/requirements"
EXPECTED_SECRET_PATH_REDACTED = "/etc/ai-ops-runner/secrets/…"


def run_device_flow_start(root: Path | None = None) -> dict[str, Any]:
    """Start device flow; return verification_url and user_code (not secrets)."""
    root = root or Path(_repo_root())
    client = _load_client(root)
    if not client:
        return {
            "ok": False,
            "error_class": "MISSING_GMAIL_CLIENT_JSON",
            "message": "Upload gmail_client.json via Settings → Connectors → Gmail OAuth, or place it at the expected secret path.",
            "requirements_endpoint": REQUIREMENTS_ENDPOINT_PATH,
            "expected_secret_path_redacted": EXPECTED_SECRET_PATH_REDACTED,
        }
    client_id, client_secret = client
    if not _HAS_URLLIB:
        return {"ok": False, "message": "urllib required for device flow."}

    body = f"client_id={urllib.parse.quote(client_id)}&scope={urllib.parse.quote(GMAIL_SCOPE)}"
    req = urllib.request.Request(
        DEVICE_CODE_URL,
        data=body.encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {"ok": False, "message": f"Device code request failed: {e.code} {e.reason}"}
    except Exception as e:
        return {"ok": False, "message": str(e)[:200]}

    device_code = data.get("device_code")
    user_code = data.get("user_code")
    verification_url = data.get("verification_url") or "https://www.google.com/device"
    interval = int(data.get("interval", 5))
    expires_in = int(data.get("expires_in", 900))

    if not device_code or not user_code:
        return {"ok": False, "message": "Missing device_code or user_code in response"}

    # Persist state for finalize (device_code, interval, client_id, client_secret)
    state_path = GMAIL_STATE_PATH
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "device_code": device_code,
        "interval": interval,
        "expires_at": time.time() + expires_in,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    try:
        state_path.write_text(json.dumps(state, indent=2))
        state_path.chmod(0o640)
    except OSError as e:
        return {"ok": False, "message": f"Cannot write state file: {e}"}

    return {
        "ok": True,
        "verification_url": verification_url,
        "user_code": user_code,
        "message": "Open verification_url and enter user_code; then run finalize.",
    }


def run_device_flow_finalize(root: Path | None = None) -> dict[str, Any]:
    """Poll for user authorization and save refresh_token to gmail_oauth.json."""
    root = root or Path(_repo_root())
    state_path = GMAIL_STATE_PATH
    if not state_path.exists():
        return {"ok": False, "message": "No device flow state; run start first."}
    try:
        state = json.loads(state_path.read_text())
    except Exception:
        return {"ok": False, "message": "Invalid device flow state file."}

    device_code = state.get("device_code")
    interval = state.get("interval", 5)
    expires_at = state.get("expires_at", 0)
    client_id = state.get("client_id")
    client_secret = state.get("client_secret")
    if not all([device_code, client_id, client_secret]):
        return {"ok": False, "message": "State missing device_code or client credentials."}

    if time.time() > expires_at:
        return {"ok": False, "message": "Device flow expired; run start again."}

    body = (
        f"client_id={urllib.parse.quote(client_id)}"
        f"&client_secret={urllib.parse.quote(client_secret)}"
        f"&device_code={urllib.parse.quote(device_code)}"
        "&grant_type=urn:ietf:params:oauth:grant-type:device_code"
    )
    req = urllib.request.Request(
        TOKEN_URL,
        data=body.encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    while time.time() <= expires_at:
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                tok_data = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            body_err = e.read().decode() if e.fp else ""
            try:
                err_json = json.loads(body_err)
                err_code = err_json.get("error")
                if err_code == "authorization_pending":
                    time.sleep(interval)
                    continue
                if err_code == "slow_down":
                    interval = min(interval + 2, 30)
                    time.sleep(interval)
                    continue
                if err_code == "expired_token":
                    return {"ok": False, "message": "Device flow expired; run start again."}
            except Exception:
                pass
            return {"ok": False, "message": f"Token request failed: {e.code}"}
        except Exception as e:
            return {"ok": False, "message": str(e)[:200]}

        refresh_token = tok_data.get("refresh_token")
        if not refresh_token:
            return {"ok": False, "message": "No refresh_token in response; re-run with access_type=offline and prompt=consent if needed."}

        # Write gmail_oauth.json (refresh_token + client_id/client_secret for harvest)
        out_path = GMAIL_OAUTH_PATH
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out = {
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        }
        out_path.write_text(json.dumps(out, indent=2))
        try:
            out_path.chmod(0o640)
        except OSError:
            pass
        try:
            state_path.unlink()
        except OSError:
            pass
        return {
            "ok": True,
            "message": "Gmail OAuth token saved; connector ready.",
            "token_fingerprint": _mask_fingerprint(refresh_token),
        }

    return {"ok": False, "message": "Device flow timed out; run start again."}
