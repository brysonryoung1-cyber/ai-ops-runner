"""
Phase 0 mirror: fetch Kalshi + Polymarket public market data, normalize, write artifacts.
Read-only; no auth. Fail-closed on config/kill_switch.
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import get_kill_switch, load_pred_markets_config, repo_root

# Redact logs: no secrets
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOG = logging.getLogger(__name__)

CANONICAL_FIELDS = [
    "venue",
    "event_id",
    "market_id",
    "outcome_id",
    "title",
    "status",
    "close_time",
    "resolution_time",
    "best_bid",
    "best_ask",
    "mid",
    "volume",
    "open_interest",
    "fetched_at_utc",
    "canonical_market_key",
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_market_key(venue: str, *ids: str) -> str:
    raw = "|".join([venue] + [str(x) for x in ids if x])
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _safe_request(url: str, headers: dict[str, str], timeout: int = 30) -> tuple[dict | list | None, str | None]:
    try:
        import urllib.request
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode()), None
    except Exception as e:
        return None, str(e)


def fetch_kalshi_markets(base_url: str, user_agent: str, limit: int = 200) -> tuple[list[dict], dict]:
    """Fetch markets list from Kalshi public API. Returns (markets, summary_extra)."""
    url = f"{base_url.rstrip('/')}/markets?limit={limit}&status=open"
    headers = {"Accept": "application/json", "User-Agent": user_agent}
    data, err = _safe_request(url, headers)
    if err:
        return [], {"kalshi_error": err, "orderbook_unavailable_without_auth": None}
    if not isinstance(data, dict) or "markets" not in data:
        return [], {"kalshi_error": "Unexpected response shape", "orderbook_unavailable_without_auth": None}
    markets = data.get("markets") or []
    return markets, {"kalshi_count": len(markets), "orderbook_unavailable_without_auth": False}


def fetch_polymarket_markets(base_url: str, user_agent: str, limit: int = 200) -> tuple[list[dict], dict]:
    """Fetch markets from Polymarket Gamma API. Returns (markets, summary_extra)."""
    url = f"{base_url.rstrip('/')}/markets?closed=false&limit={limit}"
    headers = {"Accept": "application/json", "User-Agent": user_agent}
    data, err = _safe_request(url, headers)
    if err:
        return [], {"polymarket_error": err}
    if not isinstance(data, list):
        return [], {"polymarket_error": "Unexpected response shape"}
    return data, {"polymarket_count": len(data)}


def normalize_kalshi(m: dict, fetched_at: str) -> dict:
    ticker = m.get("ticker") or m.get("market_ticker") or ""
    event_ticker = m.get("event_ticker") or ""
    key = _canonical_market_key("kalshi", event_ticker, ticker)
    yes_price = m.get("yes_price")
    no_price = m.get("no_price")
    mid = None
    if yes_price is not None and no_price is not None:
        try:
            mid = (float(yes_price) + float(no_price)) / 2
        except (TypeError, ValueError):
            pass
    return {
        "venue": "kalshi",
        "event_id": event_ticker,
        "market_id": ticker,
        "outcome_id": "",
        "title": (m.get("title") or "")[:500],
        "status": (m.get("status") or "open").lower(),
        "close_time": m.get("close_time") or "",
        "resolution_time": m.get("resolution_time") or "",
        "best_bid": m.get("yes_price"),
        "best_ask": m.get("no_price"),
        "mid": mid,
        "volume": m.get("volume"),
        "open_interest": None,
        "fetched_at_utc": fetched_at,
        "canonical_market_key": key,
    }


def normalize_polymarket(m: dict, fetched_at: str) -> list[dict]:
    """One Polymarket market can have multiple outcomes (yes/no); emit one row per outcome if applicable."""
    cid = (m.get("conditionId") or m.get("condition_id") or "")
    mid = str(m.get("id") or m.get("slug") or cid)[:128]
    title = (m.get("question") or m.get("title") or "")[:500]
    rows = []
    outcomes = m.get("outcomes") or "Yes,No"
    if isinstance(outcomes, str):
        outcomes = [s.strip() for s in outcomes.split(",")]
    elif not isinstance(outcomes, list):
        outcomes = ["Yes", "No"]
    prices = m.get("outcomePrices") or m.get("prices")
    if isinstance(prices, str):
        try:
            prices = [float(p.strip()) for p in prices.replace(" ", "").split(",") if p.strip()]
        except ValueError:
            prices = []
    elif not isinstance(prices, list):
        prices = []
    for i, out in enumerate(outcomes):
        outcome_id = str(out)[:64]
        price = prices[i] if i < len(prices) else None
        key = _canonical_market_key("polymarket", cid, mid, outcome_id)
        rows.append({
            "venue": "polymarket",
            "event_id": cid,
            "market_id": mid,
            "outcome_id": outcome_id,
            "title": title,
            "status": "closed" if m.get("closed") else "open",
            "close_time": str(m.get("endDate") or m.get("end_date") or ""),
            "resolution_time": str(m.get("resolutionDate") or ""),
            "best_bid": price,
            "best_ask": None,
            "mid": price,
            "volume": m.get("volume") or m.get("volumeNum"),
            "open_interest": None,
            "fetched_at_utc": fetched_at,
            "canonical_market_key": key,
        })
    if not rows:
        rows.append({
            "venue": "polymarket",
            "event_id": cid,
            "market_id": mid,
            "outcome_id": "",
            "title": title,
            "status": "closed" if m.get("closed") else "open",
            "close_time": str(m.get("endDate") or ""),
            "resolution_time": "",
            "best_bid": None,
            "best_ask": None,
            "mid": None,
            "volume": m.get("volume"),
            "open_interest": None,
            "fetched_at_utc": fetched_at,
            "canonical_market_key": _canonical_market_key("polymarket", cid, mid),
        })
    return rows


def write_blocked_summary(out_dir: Path, run_id: str, error_class: str, message: str) -> None:
    summary = (
        f"# pred_markets Phase 0 — Blocked\n\n"
        f"- **Run ID**: {run_id}\n"
        f"- **Error class**: {error_class}\n"
        f"- **Message**: {message}\n"
        f"- **Kill switch**: When enabled, mirror/backfill/reports write this artifact only.\n"
    )
    (out_dir / "SUMMARY.md").write_text(summary, encoding="utf-8")


def run_mirror(mode: str = "mirror_run") -> int:
    """Run mirror or bounded backfill. Returns exit code. Writes artifacts even when blocked."""
    root = repo_root()
    cfg, config_error = load_pred_markets_config(root)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]
    base_dir = Path((cfg or {}).get("artifacts", {}).get("base_dir", "artifacts/pred_markets"))
    out_dir = root / base_dir / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "logs").mkdir(exist_ok=True)

    if config_error:
        write_blocked_summary(out_dir, run_id, "CONFIG_INVALID", config_error)
        run_json = {
            "ok": False,
            "run_id": run_id,
            "error_class": "CONFIG_INVALID",
            "recommended_next_action": config_error,
        }
        (out_dir / "run.json").write_text(json.dumps(run_json, indent=2))
        print(json.dumps(run_json))
        return 1

    if get_kill_switch(root):
        write_blocked_summary(
            out_dir, run_id, "KILL_SWITCH_ENABLED",
            "Set projects.pred_markets.kill_switch to false in config/project_state.json to allow mirror."
        )
        (out_dir / "run.json").write_text(json.dumps({
            "ok": False,
            "run_id": run_id,
            "error_class": "KILL_SWITCH_ENABLED",
            "artifact_dir": str(base_dir / run_id),
        }, indent=2))
        print(json.dumps({"ok": False, "run_id": run_id, "error_class": "KILL_SWITCH_ENABLED"}))
        return 1

    fetched_at = _now_iso()
    connectors = cfg.get("connectors") or {}
    kalshi_cfg = connectors.get("kalshi") or {}
    poly_cfg = connectors.get("polymarket") or {}
    kalshi_base = kalshi_cfg.get("base_url", "https://api.elections.kalshi.com/trade-api/v2")
    poly_base = poly_cfg.get("base_url", "https://gamma-api.polymarket.com")
    kalshi_ua = kalshi_cfg.get("user_agent", "OpenClaw-pred_markets/1.0")
    poly_ua = poly_cfg.get("user_agent", "OpenClaw-pred_markets/1.0")

    all_rows: list[dict] = []
    summary: dict[str, Any] = {"run_id": run_id, "mode": mode, "fetched_at_utc": fetched_at}

    if kalshi_cfg.get("enabled", True):
        kalshi_markets, kalshi_extra = fetch_kalshi_markets(kalshi_base, kalshi_ua)
        summary.update(kalshi_extra)
        for m in kalshi_markets:
            all_rows.append(normalize_kalshi(m, fetched_at))
        # Raw-safe subset (no secrets)
        raw_kalshi = [{"ticker": m.get("ticker"), "title": (m.get("title") or "")[:200], "yes_price": m.get("yes_price"), "volume": m.get("volume")} for m in kalshi_markets]
        (out_dir / "markets_kalshi.json").write_text(json.dumps(raw_kalshi, indent=2))
    else:
        (out_dir / "markets_kalshi.json").write_text("[]")
        summary["kalshi_count"] = 0

    if poly_cfg.get("enabled", True):
        poly_markets, poly_extra = fetch_polymarket_markets(poly_base, poly_ua)
        summary.update(poly_extra)
        for m in poly_markets:
            all_rows.extend(normalize_polymarket(m, fetched_at))
        raw_poly = [{"id": m.get("id"), "question": (m.get("question") or m.get("title") or "")[:200], "closed": m.get("closed")} for m in poly_markets]
        (out_dir / "markets_polymarket.json").write_text(json.dumps(raw_poly, indent=2))
    else:
        (out_dir / "markets_polymarket.json").write_text("[]")
        summary["polymarket_count"] = 0

    summary["canonical_rows"] = len(all_rows)

    # snapshots.csv
    if all_rows:
        with (out_dir / "snapshots.csv").open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CANONICAL_FIELDS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(all_rows)

    # SUMMARY.md
    summary_md = (
        f"# pred_markets Phase 0 Mirror — {mode}\n\n"
        f"- **Run ID**: {run_id}\n"
        f"- **Fetched at**: {fetched_at}\n"
        f"- **Kalshi markets**: {summary.get('kalshi_count', 0)}\n"
        f"- **Polymarket markets**: {summary.get('polymarket_count', 0)}\n"
        f"- **Canonical rows**: {len(all_rows)}\n"
    )
    if summary.get("kalshi_error"):
        summary_md += f"- **Kalshi error**: {summary['kalshi_error']}\n"
    if summary.get("polymarket_error"):
        summary_md += f"- **Polymarket error**: {summary['polymarket_error']}\n"
    (out_dir / "SUMMARY.md").write_text(summary_md)

    # run.json
    run_json = {
        "ok": True,
        "run_id": run_id,
        "mode": mode,
        "artifact_dir": str(base_dir / run_id),
        "summary": summary,
        "started_at": fetched_at,
        "finished_at": _now_iso(),
    }
    (out_dir / "run.json").write_text(json.dumps(run_json, indent=2))

    # Redacted log (no secrets)
    log_path = out_dir / "logs" / "redacted.log"
    log_path.write_text(f"[{fetched_at}] mirror mode={mode} canonical_rows={len(all_rows)}\n")

    # Update project_state last_mirror_*
    state_path = root / "config" / "project_state.json"
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text())
            proj = state.setdefault("projects", {}).setdefault("pred_markets", {})
            proj["last_mirror_run_id"] = run_id
            proj["last_mirror_timestamp"] = _now_iso()
            proj["phase0_baseline_artifact_dir"] = str(base_dir / run_id)
            state_path.write_text(json.dumps(state, indent=2))
        except Exception:
            pass

    print(json.dumps({"ok": True, "run_id": run_id, "artifact_paths": [str(base_dir / run_id)]}))
    return 0


def main() -> int:
    mode = "mirror_run"
    if len(sys.argv) > 1:
        mode = sys.argv[1]
    if mode not in ("mirror_run", "mirror_backfill"):
        mode = "mirror_run"
    return run_mirror(mode=mode)


if __name__ == "__main__":
    sys.exit(main())
