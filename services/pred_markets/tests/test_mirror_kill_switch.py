"""Hermetic tests: kill_switch blocks actions but writes blocked artifact SUMMARY.md."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_kill_switch_blocks_mirror_and_writes_summary_md(tmp_path: Path) -> None:
    """When kill_switch is true, mirror run writes blocked SUMMARY.md and returns error_class."""
    (tmp_path / "config" / "projects").mkdir(parents=True)
    cfg = {
        "project_id": "pred_markets",
        "phase": 0,
        "kill_switch": True,
        "connectors": {
            "kalshi": {"enabled": True, "base_url": "https://api.elections.kalshi.com/trade-api/v2", "mode": "public"},
            "polymarket": {"enabled": True, "base_url": "https://gamma-api.polymarket.com", "mode": "public"},
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
            "properties": {"project_id": {}, "phase": {}, "kill_switch": {}, "connectors": {}, "artifacts": {"type": "object", "properties": {"base_dir": {}}}},
        }))
    (tmp_path / "config" / "project_state.json").write_text(json.dumps({
        "projects": {"pred_markets": {"kill_switch": True}},
    }, indent=2))

    os.environ["OPENCLAW_REPO_ROOT"] = str(tmp_path)
    from services.pred_markets import mirror
    rc = mirror.run_mirror(mode="mirror_run")
    assert rc != 0

    # Blocked run still writes artifact dir with SUMMARY.md and run.json with error_class
    base = tmp_path / "artifacts" / "pred_markets"
    dirs = list(base.iterdir()) if base.exists() else []
    assert len(dirs) >= 1
    run_dir = dirs[-1]
    summary = run_dir / "SUMMARY.md"
    assert summary.exists()
    text = summary.read_text()
    assert "KILL_SWITCH" in text or "Blocked" in text
    run_json_path = run_dir / "run.json"
    assert run_json_path.exists()
    run_data = json.loads(run_json_path.read_text())
    assert run_data.get("error_class") == "KILL_SWITCH_ENABLED"
    assert run_data.get("ok") is False
