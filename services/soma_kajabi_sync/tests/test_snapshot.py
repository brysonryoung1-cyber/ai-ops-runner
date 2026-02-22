"""Hermetic tests for snapshot module — validation, error classes, no network."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

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


def test_load_kajabi_products_uses_discovered():
    """When kajabi_products.json exists, load_kajabi_products returns discovered mapping."""
    from unittest.mock import patch

    from soma_kajabi_sync.config import load_kajabi_products

    with tempfile.TemporaryDirectory() as tmp:
        products_path = Path(tmp) / "kajabi_products.json"
        products_path.write_text(json.dumps({
            "products": {
                "Home User Library": "discovered-home-slug",
                "Practitioner Library": "discovered-practitioner-slug",
            },
            "captured_at": "2025-01-01T00:00:00Z",
        }))
        with patch("soma_kajabi_sync.config.KAJABI_PRODUCTS_PATH", products_path):
            result = load_kajabi_products()
        assert result["Home User Library"] == "discovered-home-slug"
        assert result["Practitioner Library"] == "discovered-practitioner-slug"


def test_load_kajabi_products_fallback_when_missing():
    """When kajabi_products.json missing, load_kajabi_products falls back to KAJABI_PRODUCTS."""
    from soma_kajabi_sync.config import KAJABI_PRODUCTS, load_kajabi_products

    with tempfile.TemporaryDirectory() as tmp:
        missing = Path(tmp) / "nonexistent.json"
        assert not missing.exists()
        with patch("soma_kajabi_sync.config.KAJABI_PRODUCTS_PATH", missing):
            result = load_kajabi_products()
        assert result == KAJABI_PRODUCTS
