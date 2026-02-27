#!/usr/bin/env python3
"""
Unit tests for /api/ui/version drift logic (fail-closed).
Verifies: origin_main_* missing -> drift_status=unknown; tree match -> drift=false; mismatch -> drift=true.
"""
import json
import tempfile
from pathlib import Path

import pytest


def _compute_drift_response(
    deployed_head: str | None,
    deployed_tree: str | None,
    shipped_head: str | None,
    shipped_tree: str | None,
    shipped_at: str | None,
    stale_days: int = 7,
) -> dict:
    """Simulate version route drift logic."""
    origin_tree = shipped_tree[:40] if shipped_tree else None
    origin_head = shipped_head[:40] if shipped_head else None

    # Staleness check
    stale = False
    if shipped_at:
        try:
            from datetime import datetime, timezone

            shipped = datetime.fromisoformat(shipped_at.replace("Z", "+00:00")).timestamp()
            now = datetime.now(timezone.utc).timestamp()
            days = (now - shipped) / (24 * 60 * 60)
            stale = days > stale_days
        except Exception:
            stale = True
    else:
        stale = True

    if stale or (not shipped_tree and not shipped_head):
        origin_tree = None
        origin_head = None

    cannot_compute = not origin_tree and not origin_head
    ship_info_unusable = stale or (not shipped_tree and not shipped_head)

    if cannot_compute or ship_info_unusable:
        return {
            "drift_status": "unknown",
            "drift": None,
            "drift_reason": "origin_main_tree_sha unavailable (ship_info.json missing/stale, git unavailable in container)",
        }

    deployed_tree_norm = deployed_tree[:40] if deployed_tree else None
    deployed_head_norm = deployed_head[:40] if deployed_head else None

    if origin_tree and deployed_tree_norm:
        drift = deployed_tree_norm != origin_tree
        return {
            "drift_status": "ok",
            "drift": drift,
            "drift_reason": "deployed_tree_sha != origin_main_tree_sha" if drift else None,
        }
    if origin_head and deployed_head_norm:
        drift = deployed_head_norm != origin_head
        return {
            "drift_status": "ok",
            "drift": drift,
            "drift_reason": "deployed_head_sha != origin_main_head_sha (tree unavailable)" if drift else None,
        }
    return {
        "drift_status": "unknown",
        "drift": None,
        "drift_reason": "insufficient data for tree comparison",
    }


def test_origin_missing_drift_status_unknown():
    """When origin_main_* missing, drift_status=unknown, drift=null (fail-closed)."""
    r = _compute_drift_response(
        deployed_head="a" * 40,
        deployed_tree="b" * 40,
        shipped_head=None,
        shipped_tree=None,
        shipped_at=None,
    )
    assert r["drift_status"] == "unknown"
    assert r["drift"] is None


def test_origin_missing_never_drift_false():
    """When origin_main_tree_sha missing, drift must NEVER be false."""
    r = _compute_drift_response(
        deployed_head="a" * 40,
        deployed_tree="b" * 40,
        shipped_head=None,
        shipped_tree=None,
        shipped_at=None,
    )
    assert r["drift"] is not False


def test_trees_match_drift_false():
    """When shipped_tree_sha == deployed_tree_sha, drift=false, drift_status=ok."""
    from datetime import datetime, timezone

    tree = "c" * 40
    recent_ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    r = _compute_drift_response(
        deployed_head="a" * 40,
        deployed_tree=tree,
        shipped_head="a" * 40,
        shipped_tree=tree,
        shipped_at=recent_ts,
        stale_days=30,
    )
    assert r["drift_status"] == "ok"
    assert r["drift"] is False


def test_trees_mismatch_drift_true():
    """When shipped_tree_sha != deployed_tree_sha, drift=true, drift_status=ok."""
    from datetime import datetime, timezone

    recent_ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    r = _compute_drift_response(
        deployed_head="a" * 40,
        deployed_tree="b" * 40,
        shipped_head="a" * 40,
        shipped_tree="c" * 40,
        shipped_at=recent_ts,
        stale_days=30,
    )
    assert r["drift_status"] == "ok"
    assert r["drift"] is True
    assert "tree" in (r["drift_reason"] or "")
