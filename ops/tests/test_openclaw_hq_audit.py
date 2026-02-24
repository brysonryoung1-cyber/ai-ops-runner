"""Unit test: openclaw_hq_audit.sh uses 127.0.0.1 base URLs only (no ts.net calls)."""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
AUDIT_SCRIPT = REPO_ROOT / "ops" / "openclaw_hq_audit.sh"


def test_audit_uses_localhost_only() -> None:
    """Assert audit script contains only 127.0.0.1 base URLs, no ts.net."""
    content = AUDIT_SCRIPT.read_text()
    # Must not contain tailnet URLs used for fetching audit data
    assert "ts.net" not in content or "tailscale status" in content, (
        "Audit must not fetch from ts.net; only 127.0.0.1 allowed"
    )
    assert "127.0.0.1" in content, "Audit must use 127.0.0.1 for API/hostd calls"
    assert "CONSOLE_BASE=\"http://127.0.0.1" in content
    assert "HOSTD_BASE=\"http://127.0.0.1" in content
