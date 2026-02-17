"""Hermetic tests: schema load success/failure for pred_markets config."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_config_load_success(tmp_path: Path) -> None:
    """Valid config and schema load successfully."""
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
    schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "required": ["project_id", "phase", "kill_switch", "connectors", "artifacts"],
        "properties": {
            "project_id": {"const": "pred_markets"},
            "phase": {"type": "integer"},
            "kill_switch": {"type": "boolean"},
            "connectors": {"type": "object"},
            "artifacts": {"type": "object", "required": ["base_dir"], "properties": {"base_dir": {"type": "string"}}},
        },
    }
    (tmp_path / "config" / "projects" / "pred_markets.json").write_text(json.dumps(cfg))
    (tmp_path / "config" / "projects" / "pred_markets.schema.json").write_text(json.dumps(schema))
    (tmp_path / "config" / "project_state.json").write_text(json.dumps({"projects": {"pred_markets": {"kill_switch": True}}}))

    import os
    os.environ["OPENCLAW_REPO_ROOT"] = str(tmp_path)
    from services.pred_markets.config import load_pred_markets_config, repo_root

    root = repo_root()
    assert str(root) == str(tmp_path)
    loaded, err = load_pred_markets_config(root)
    assert err is None
    assert loaded is not None
    assert loaded["project_id"] == "pred_markets"
    assert loaded["connectors"]["kalshi"]["mode"] == "public"


def test_config_load_failure_missing_file(tmp_path: Path) -> None:
    """Missing config file returns error."""
    (tmp_path / "config" / "projects").mkdir(parents=True)
    (tmp_path / "config" / "project_state.json").write_text("{}")
    import os
    os.environ["OPENCLAW_REPO_ROOT"] = str(tmp_path)
    from services.pred_markets.config import load_pred_markets_config, repo_root

    loaded, err = load_pred_markets_config(repo_root())
    assert loaded is None
    assert err is not None
    assert "missing" in err.lower() or "pred_markets" in err
