"""Hermetic tests for snapshot module — validation, error classes, no network."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest


def test_validate_storage_state_has_kajabi_cookies_missing():
    """Storage state missing or empty returns (False, msg)."""
    from soma_kajabi_sync.snapshot import _validate_storage_state_has_kajabi_cookies

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "missing.json"
        valid, msg = _validate_storage_state_has_kajabi_cookies(path)
    assert valid is False
    assert "missing" in msg or "empty" in msg


def test_validate_storage_state_has_kajabi_cookies_no_kajabi():
    """Storage state without kajabi.com cookies returns False."""
    from soma_kajabi_sync.snapshot import _validate_storage_state_has_kajabi_cookies

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "state.json"
        path.write_text(json.dumps({"cookies": [{"name": "x", "domain": "example.com", "value": "y"}]}))
        valid, msg = _validate_storage_state_has_kajabi_cookies(path)
    assert valid is False
    assert "kajabi" in msg.lower()


def test_validate_storage_state_has_kajabi_cookies_valid():
    """Storage state with app.kajabi.com cookie returns True."""
    from soma_kajabi_sync.snapshot import _validate_storage_state_has_kajabi_cookies

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "state.json"
        path.write_text(json.dumps({
            "cookies": [
                {"name": "_kjb_session", "domain": ".kajabi.com", "value": "abc"},
            ]
        }))
        valid, msg = _validate_storage_state_has_kajabi_cookies(path)
    assert valid is True
    assert msg == "ok"


def test_kajabi_snapshot_error():
    """KajabiSnapshotError has error_class and message."""
    from soma_kajabi_sync.snapshot import KajabiSnapshotError

    e = KajabiSnapshotError("KAJABI_NOT_LOGGED_IN", "Session expired")
    assert e.error_class == "KAJABI_NOT_LOGGED_IN"
    assert "expired" in e.message
    assert str(e) == "Session expired"


def test_snapshot_smoke_writes_artifacts():
    """Smoke mode produces valid snapshot.json without credentials."""
    from soma_kajabi_sync.snapshot import snapshot_kajabi

    result = snapshot_kajabi("Home User Library", smoke=True)
    assert result["status"] == "success"
    assert result["total_categories"] >= 1
    assert result["total_items"] >= 1
    artifacts_dir = Path(result["artifacts_dir"])
    assert (artifacts_dir / "snapshot.json").exists()
    data = json.loads((artifacts_dir / "snapshot.json").read_text())
    assert data["product"] == "Home User Library"
    assert "categories" in data
