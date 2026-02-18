"""Parse NT8 Strategy Analyzer (or mock) exports into tier2/results.csv and summary.json.

Expects raw_exports/ to contain CSV exports with columns we can map to RESULTS_COLUMNS.
If no recognizable export is found, returns stub row and FAIL with reason.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from tools.tier2_artifacts import RESULTS_COLUMNS, Tier2Artifacts


# Common NT8 / backtest CSV column name variants (case-insensitive match)
_PNL_KEYS = ("net profit", "pnl", "netprofit", "profit", "realized pnl")
_PF_KEYS = ("profit factor", "profitfactor", "pf")
_SHARPE_KEYS = ("sharpe ratio", "sharpe", "sharperatio")
_MAXDD_KEYS = ("max drawdown", "maxdrawdown", "max dd", "maxdd", "drawdown")
_TRADES_KEYS = ("trades", "trade count", "number of trades", "numtrades")
_WINRATE_KEYS = ("win rate", "winrate", "percent profitable", "win %")
_AVG_TRADE_KEYS = ("avg trade", "avgtrade", "average trade")
_EXPECTANCY_KEYS = ("expectancy",)
_TIME_IN_MARKET_KEYS = ("time in market", "timeinmarket", "exposure %")


def _float(s: str | None) -> str:
    if s is None or s == "":
        return ""
    try:
        return str(float(s))
    except (ValueError, TypeError):
        return s.strip()


def _get_col(row: dict[str, str], keys: tuple[str, ...]) -> str:
    for k, v in row.items():
        if k and (k.strip().lower() in {key.lower() for key in keys}):
            return (v or "").strip()
    return ""


def parse_export_csv(csv_path: Path) -> dict[str, str] | None:
    """Parse a single CSV export; return one row dict with RESULTS_COLUMNS keys, or None."""
    try:
        with csv_path.open(newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
    except Exception:
        return None
    if not rows:
        return None
    row = rows[0]

    pnl = _get_col(row, _PNL_KEYS)
    pf = _get_col(row, _PF_KEYS)
    sharpe = _get_col(row, _SHARPE_KEYS)
    max_dd = _get_col(row, _MAXDD_KEYS)
    trades = _get_col(row, _TRADES_KEYS)
    winrate = _get_col(row, _WINRATE_KEYS)
    avg_trade = _get_col(row, _AVG_TRADE_KEYS)
    expectancy = _get_col(row, _EXPECTANCY_KEYS)
    time_in_market = _get_col(row, _TIME_IN_MARKET_KEYS)

    return {
        "candidate_id": "",
        "pnl": _float(pnl) if pnl else "",
        "pf": _float(pf) if pf else "",
        "sharpe": _float(sharpe) if sharpe else "",
        "max_dd": _float(max_dd) if max_dd else "",
        "trades": trades,
        "winrate": _float(winrate) if winrate else "",
        "avg_trade": _float(avg_trade) if avg_trade else "",
        "expectancy": _float(expectancy) if expectancy else "",
        "profit_factor": _float(pf) if pf else "",
        "time_in_market": _float(time_in_market) if time_in_market else "",
    }


def normalize_raw_exports(
    tier2_artifacts: Tier2Artifacts,
    candidate_id: str,
) -> tuple[list[dict[str, Any]], str, list[str]]:
    """
    Scan tier2/raw_exports/ for CSVs, parse into result rows.
    Returns (rows for results.csv, verdict PASS/FAIL, reasons).
    """
    raw = tier2_artifacts.tier2_dir / "raw_exports"
    rows: list[dict[str, Any]] = []
    if raw.is_dir():
        for f in raw.iterdir():
            if f.suffix.lower() == ".csv":
                parsed = parse_export_csv(f)
                if parsed:
                    parsed["candidate_id"] = candidate_id
                    rows.append(parsed)
                    break  # one candidate = one row
    if not rows:
        return [], "FAIL", ["No parseable NT8 export in raw_exports/"]
    return rows, "PASS", []
