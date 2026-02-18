"""Build confirm_spec.json from topk.json for the NT8 confirmation harness.

Canonical fields: candidate_id, params (flat NT8 case-sensitive names + typed values),
instrument, timeframe, date_ranges, mode. Consumed by NT8 AddOn or mock harness.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def topk_to_confirm_spec(topk: dict[str, Any], mode: str = "strategy_analyzer") -> dict[str, Any]:
    """Derive confirm_spec from topk. Params stay case-sensitive with typed values."""
    params_flat: dict[str, Any] = {}
    for name, spec in (topk.get("params") or {}).items():
        if isinstance(spec, dict) and "value" in spec:
            params_flat[name] = spec["value"]
        else:
            params_flat[name] = spec

    return {
        "candidate_id": topk["candidate_id"],
        "strategy_name": topk["strategy_name"],
        "strategy_version": topk.get("strategy_version", ""),
        "instrument": topk["instrument"],
        "timeframe": topk["timeframe"],
        "date_ranges": topk["date_ranges"],
        "sessions": topk.get("sessions", ""),
        "params": params_flat,
        "fees_slippage": topk.get("fees_slippage", {}),
        "mode": mode,
        "BACKTEST_ONLY": True,
    }


def write_confirm_spec(topk_path: str | Path, job_dir: str | Path, mode: str = "strategy_analyzer") -> Path:
    """Load topk from path, build confirm_spec, write to job_dir/confirm_spec.json. Returns path."""
    with open(topk_path) as f:
        topk = json.load(f)
    spec = topk_to_confirm_spec(topk, mode=mode)
    job = Path(job_dir)
    job.mkdir(parents=True, exist_ok=True)
    out = job / "confirm_spec.json"
    out.write_text(json.dumps(spec, indent=2) + "\n")
    return out
