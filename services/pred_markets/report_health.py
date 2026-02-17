"""Health report: config + connector reachability. Writes SUMMARY.md; blocked when kill_switch or CONFIG_INVALID."""

from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .config import get_kill_switch, load_pred_markets_config, repo_root


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> int:
    root = repo_root()
    cfg, config_error = load_pred_markets_config(root)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]
    base_dir = Path((cfg or {}).get("artifacts", {}).get("base_dir", "artifacts/pred_markets"))
    out_dir = root / base_dir / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    if config_error:
        (out_dir / "SUMMARY.md").write_text(
            f"# pred_markets Health — Blocked\n\n- **Error class**: CONFIG_INVALID\n- **Message**: {config_error}\n"
        )
        (out_dir / "run.json").write_text(json.dumps({
            "ok": False, "run_id": run_id, "error_class": "CONFIG_INVALID", "recommended_next_action": config_error
        }, indent=2))
        print(json.dumps({"ok": False, "run_id": run_id, "error_class": "CONFIG_INVALID"}))
        return 1

    if get_kill_switch(root):
        (out_dir / "SUMMARY.md").write_text(
            "# pred_markets Health — Blocked\n\n- **Error class**: KILL_SWITCH_ENABLED\n"
        )
        (out_dir / "run.json").write_text(json.dumps({
            "ok": False, "run_id": run_id, "error_class": "KILL_SWITCH_ENABLED"
        }, indent=2))
        print(json.dumps({"ok": False, "run_id": run_id, "error_class": "KILL_SWITCH_ENABLED"}))
        return 1

    # Minimal health: connectors reachable (HEAD/GET)
    connectors = cfg.get("connectors") or {}
    kalshi_base = (connectors.get("kalshi") or {}).get("base_url", "https://api.elections.kalshi.com/trade-api/v2")
    poly_base = (connectors.get("polymarket") or {}).get("base_url", "https://gamma-api.polymarket.com")
    try:
        import urllib.request
        kalshi_ok = False
        try:
            req = urllib.request.Request(f"{kalshi_base.rstrip('/')}/markets?limit=1", method="GET")
            req.add_header("User-Agent", "OpenClaw-pred_markets/1.0")
            with urllib.request.urlopen(req, timeout=10) as _:
                kalshi_ok = True
        except Exception:
            pass
        poly_ok = False
        try:
            req = urllib.request.Request(f"{poly_base.rstrip('/')}/markets?limit=1", method="GET")
            req.add_header("User-Agent", "OpenClaw-pred_markets/1.0")
            with urllib.request.urlopen(req, timeout=10) as _:
                poly_ok = True
        except Exception:
            pass
    except Exception:
        kalshi_ok = poly_ok = False

    summary = f"# pred_markets Health Report\n\n- **Run ID**: {run_id}\n- **Kalshi reachable**: {kalshi_ok}\n- **Polymarket reachable**: {poly_ok}\n"
    (out_dir / "SUMMARY.md").write_text(summary)
    (out_dir / "run.json").write_text(json.dumps({
        "ok": True, "run_id": run_id, "kalshi_reachable": kalshi_ok, "polymarket_reachable": poly_ok
    }, indent=2))
    print(json.dumps({"ok": True, "run_id": run_id}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
