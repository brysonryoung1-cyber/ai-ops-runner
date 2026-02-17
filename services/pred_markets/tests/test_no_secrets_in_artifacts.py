"""Hermetic tests: no secret-like strings in artifact logs."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_no_secret_like_strings_in_artifact_logs(tmp_path: Path) -> None:
    """After a mirror run, redacted.log and SUMMARY.md must not contain key/token/secret (basic grep)."""
    (tmp_path / "config" / "projects").mkdir(parents=True)
    cfg = {
        "project_id": "pred_markets",
        "phase": 0,
        "kill_switch": False,
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
        (tmp_path / "config" / "projects" / "pred_markets.schema.json").write_text(json.dumps({"type": "object", "required": [], "properties": {}}))
    (tmp_path / "config" / "project_state.json").write_text(json.dumps({"projects": {"pred_markets": {"kill_switch": False}}}, indent=2))

    import os
    os.environ["OPENCLAW_REPO_ROOT"] = str(tmp_path)
    from services.pred_markets import mirror
    with patch.object(mirror, "_safe_request") as mock_req:
        def safe_mock(url, headers, timeout=30):
            if "kalshi" in url or "elections" in url:
                return ({"markets": []}, None)
            return ([], None)
        mock_req.side_effect = safe_mock
        mirror.run_mirror(mode="mirror_run")

    base = tmp_path / "artifacts" / "pred_markets"
    dirs = list(base.iterdir()) if base.exists() else []
    assert len(dirs) >= 1
    run_dir = dirs[-1]
    # Avoid writing raw secrets (basic grep; allow words in descriptive text)
    forbidden = ["sk-proj-", "sk-", "ghp_", "api_key=", "password="]
    for name in ["SUMMARY.md", "run.json"]:
        p = run_dir / name
        if p.exists():
            text = p.read_text().lower()
            for word in forbidden:
                assert word not in text, f"Found forbidden substring '{word}' in {name}"
    log_dir = run_dir / "logs"
    if log_dir.exists():
        for f in log_dir.iterdir():
            if f.is_file():
                text = f.read_text().lower()
                for word in forbidden:
                    assert word not in text, f"Found forbidden substring '{word}' in logs/{f.name}"
