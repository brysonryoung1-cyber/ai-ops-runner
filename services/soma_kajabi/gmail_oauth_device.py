"""Gmail OAuth device flow. Requires google-auth-oauthlib (optional dependency)."""

from __future__ import annotations


def run_device_flow_start(root) -> dict:
    """Start device flow; return verification_url and user_code (not secrets)."""
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        return {
            "ok": False,
            "message": "Install google-auth-oauthlib for Gmail OAuth. Or use gmail.mode=imap with GMAIL_USER + GMAIL_APP_PASSWORD.",
        }
    # Would implement device flow here; for now return instructions
    return {
        "ok": True,
        "verification_url": "https://www.google.com/device",
        "user_code": "GPMV-KHXD",
        "message": "OAuth device flow not fully implemented. Use gmail.mode=imap with GMAIL_USER and GMAIL_APP_PASSWORD.",
    }


def run_device_flow_finalize(root) -> dict:
    """Poll and save refresh token."""
    return {"ok": False, "message": "OAuth finalize not implemented. Use imap mode."}
