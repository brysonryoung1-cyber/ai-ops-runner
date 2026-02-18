"""Unit tests for hostd connector secrets upload.

Tests:
  - Reject non-allowlisted filename
  - Reject oversize content
  - Reject invalid JSON content
  - Accept valid gmail_client.json and write with 0600 (using temp dir)
"""

import base64
import json
import os
import sys
import tempfile
from unittest.mock import patch

# Add ops/ to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from openclaw_hostd import handle_secrets_upload, _load_secrets_allowlist


def test_reject_non_allowlisted_filename():
    """Upload with filename not in allowlist returns 403."""
    body = json.dumps({"filename": "other.json", "content": base64.b64encode(b"{}").decode()}).encode()
    status, resp = handle_secrets_upload(body)
    assert status == 403
    assert resp.get("ok") is False
    assert "allowlist" in resp.get("error", "").lower() or "not allowlisted" in resp.get("error", "").lower()


def test_reject_oversize():
    """Content larger than max_size returns 400."""
    uploads, max_size = _load_secrets_allowlist()
    big = json.dumps({"client_id": "x", "client_secret": "y"}).encode() + b"x" * (max_size + 1)
    body = json.dumps({"filename": "gmail_client.json", "content": base64.b64encode(big).decode()}).encode()
    status, resp = handle_secrets_upload(body)
    assert status == 400
    assert resp.get("ok") is False
    assert "exceeds" in resp.get("error", "").lower() or "128" in resp.get("error", "")


def test_reject_invalid_json():
    """Content that is not valid JSON returns 400."""
    raw = b'{"client_id": "a", invalid}'
    body = json.dumps({"filename": "gmail_client.json", "content": base64.b64encode(raw).decode()}).encode()
    status, resp = handle_secrets_upload(body)
    assert status == 400
    assert resp.get("ok") is False
    assert "json" in resp.get("error", "").lower()


def test_accept_valid_gmail_client_json():
    """Valid gmail_client.json is written to allowlisted path with 0600."""
    valid = json.dumps({"installed": {"client_id": "cid", "client_secret": "csec"}}).encode()
    with tempfile.TemporaryDirectory() as tmp:
        target = os.path.join(tmp, "gmail_client.json")

        def fake_allowlist():
            return ({"gmail_client.json": target}, 131072)

        with patch("openclaw_hostd._load_secrets_allowlist", side_effect=fake_allowlist):
            body = json.dumps({
                "filename": "gmail_client.json",
                "content": base64.b64encode(valid).decode(),
            }).encode()
            status, resp = handle_secrets_upload(body)

        assert status == 200
        assert resp.get("ok") is True
        assert "saved_path" in resp
        assert "fingerprint" in resp
        assert "next_steps" in resp
        assert os.path.isfile(target)
        with open(target, "rb") as f:
            assert f.read() == valid
        mode = os.stat(target).st_mode & 0o777
        assert mode == 0o600, f"Expected 0600, got {oct(mode)}"
