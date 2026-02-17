"""Hermetic tests: mirror writes expected artifact files when using mocked HTTP responses."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_mirror_writes_artifacts_with_mocked_http(tmp_path: Path) -> None:
    """When HTTP is mocked to return market data, mirror writes run.json, SUMMARY.md, markets_*.json, snapshots.csv."""
    (tmp_path / "config" / "projects").mkdir(parents=True)
    cfg = {
        "project_id": "pred_markets",
        "phase": 0,
        "kill_switch": False,
        "connectors": {
            "kalshi": {"enabled": True, "base_url": "https://api.elections.kalshi.com/trade-api/v2", "mode": "public", "user_agent": "test"},
            "polymarket": {"enabled": True, "base_url": "https://gamma-api.polymarket.com", "mode": "public", "user_agent": "test"},
        },
        "artifacts": {"base_dir": "artifacts/pred_markets"},
    }
    schema_path = _repo_root() / "config" / "projects" / "pred_markets.schema.json"
    (tmp_path / "config" / "projects" / "pred_markets.json").write_text(json.dumps(cfg))
    if schema_path.exists():
        (tmp_path / "config" / "projects" / "pred_markets.schema.json").write_text(schema_path.read_text())
    else:
        (tmp_path / "config" / "projects" / "pred_markets.schema.json").write_text(json.dumps({
            "type": "object", "required": ["project_id", "phase", "kill_switch", "connectors", "artifacts"],
            "properties": {"project_id": {}, "phase": {}, "kill_switch": {}, "connectors": {}, "artifacts": {}},
        }))
    (tmp_path / "config" / "project_state.json").write_text(json.dumps({
        "projects": {"pred_markets": {"kill_switch": False}},
    }, indent=2))

    import os
    os.environ["OPENCLAW_REPO_ROOT"] = str(tmp_path)
    from services.pred_markets import mirror
    with patch.object(mirror, "_safe_request") as mock_req:
        def safe_mock(url, headers, timeout=30):
            if "kalshi" in url or "elections" in url:
                return ({"markets": [{"ticker": "T1", "event_ticker": "E1", "title": "Test", "yes_price": 50, "no_price": 50, "volume": 100, "status": "open"}]}, None)
            return ([{"id": "1", "conditionId": "c1", "question": "Q1", "closed": False, "outcomes": "Yes,No", "outcomePrices": "0.5,0.5"}], None)
        mock_req.side_effect = safe_mock
        rc = mirror.run_mirror(mode="mirror_run")

    assert rc == 0
    base = tmp_path / "artifacts" / "pred_markets"
    dirs = sorted(base.iterdir()) if base.exists() else []
    assert len(dirs) >= 1
    run_dir = dirs[-1]
    assert (run_dir / "run.json").exists()
    assert (run_dir / "SUMMARY.md").exists()
    assert (run_dir / "markets_kalshi.json").exists()
    assert (run_dir / "markets_polymarket.json").exists()
    assert (run_dir / "snapshots.csv").exists()
    run_data = json.loads((run_dir / "run.json").read_text())
    assert run_data.get("ok") is True
    summary = (run_dir / "SUMMARY.md").read_text()
    assert "canonical" in summary.lower() or "Kalshi" in summary or "row" in summary
