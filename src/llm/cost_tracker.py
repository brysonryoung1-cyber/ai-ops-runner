"""Always-on API cost tracking and guardrails.

Per-request logging to artifacts/cost/usage.jsonl.
Rollups by day, project_id, action, model.
Guard: hourly_usd_limit, daily_usd_limit; block non-essential LLM when tripped.
Secrets only in /etc/ai-ops-runner/secrets; never in logs/artifacts.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any


def _repo_root() -> Path:
    root = os.environ.get("OPENCLAW_REPO_ROOT")
    if root and Path(root).exists():
        return Path(root)
    cwd = Path.cwd()
    for _ in range(10):
        if (cwd / "config" / "project_state.json").exists():
            return cwd
        if cwd == cwd.parent:
            break
        cwd = cwd.parent
    return Path(root or "/opt/ai-ops-runner")


def _cost_dir() -> Path:
    return _repo_root() / "artifacts" / "cost"


def _guard_dir() -> Path:
    return _repo_root() / "artifacts" / "cost_guard"


def load_guard_config() -> dict[str, float]:
    """Load hourly_usd_limit, daily_usd_limit from config. Defaults: 20, 100."""
    root = _repo_root()
    for name in ("cost_guard.json", "llm.json"):
        path = root / "config" / name
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text())
            if name == "cost_guard.json":
                return {
                    "hourly_usd_limit": float(data.get("hourly_usd_limit", 20)),
                    "daily_usd_limit": float(data.get("daily_usd_limit", 100)),
                }
            budget = data.get("budget") or data
            return {
                "hourly_usd_limit": float(budget.get("hourly_usd_limit", 20)),
                "daily_usd_limit": float(budget.get("daily_usd_limit", 100)),
            }
        except Exception:
            continue
    return {"hourly_usd_limit": 20.0, "daily_usd_limit": 100.0}


def log_usage(
    project_id: str,
    action: str,
    model: str,
    provider: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    cost_usd: float,
) -> None:
    """Append one usage record to artifacts/cost/usage.jsonl."""
    now = datetime.now(timezone.utc)
    record = {
        "timestamp_utc": now.isoformat(),
        "date": now.strftime("%Y-%m-%d"),
        "hour": now.strftime("%Y-%m-%dT%H"),
        "project_id": project_id,
        "action": action,
        "model": model,
        "provider": provider,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "cost_usd": round(cost_usd, 6),
    }
    d = _cost_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / "usage.jsonl"
    try:
        with path.open("a") as f:
            f.write(json.dumps(record) + "\n")
    except OSError:
        pass


def _read_usage_lines(limit_lines: int | None = None) -> list[dict]:
    """Read usage.jsonl and return list of records (newest last if limit)."""
    path = _cost_dir() / "usage.jsonl"
    if not path.exists():
        return []
    records = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if limit_lines is not None and len(records) > limit_lines:
        records = records[-limit_lines:]
    return records


def rollups_by_day(days: int = 30) -> dict[str, Any]:
    """Aggregate usage by day (and optionally by project, action, model)."""
    records = _read_usage_lines()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    by_day: dict[str, float] = defaultdict(float)
    by_project: dict[str, float] = defaultdict(float)
    by_action: dict[str, float] = defaultdict(float)
    by_model: dict[str, float] = defaultdict(float)
    for r in records:
        date = r.get("date") or r.get("timestamp_utc", "")[:10]
        if date < cutoff:
            continue
        cost = float(r.get("cost_usd", 0))
        by_day[date] += cost
        by_project[r.get("project_id") or "default"] += cost
        by_action[r.get("action") or "unknown"] += cost
        by_model[r.get("model") or "unknown"] += cost
    return {
        "by_day": dict(by_day),
        "by_project": dict(by_project),
        "by_action": dict(by_action),
        "by_model": dict(by_model),
    }


def spend_today() -> float:
    """Total cost today (UTC)."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    records = _read_usage_lines()
    return sum(float(r.get("cost_usd", 0)) for r in records if (r.get("date") or r.get("timestamp_utc", "")[:10]) == today)


def spend_mtd() -> float:
    """Total cost month-to-date (UTC)."""
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_start_str = month_start.strftime("%Y-%m-%d")
    records = _read_usage_lines()
    return sum(
        float(r.get("cost_usd", 0))
        for r in records
        if (r.get("date") or r.get("timestamp_utc", "")[:10]) >= month_start_str
    )


def spend_last_n_hours(n: int) -> float:
    """Total cost in the last n hours (UTC)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=n)).strftime("%Y-%m-%dT%H")
    records = _read_usage_lines()
    return sum(
        float(r.get("cost_usd", 0))
        for r in records
        if (r.get("hour") or r.get("timestamp_utc", "")[:13]) >= cutoff
    )


def check_guard(run_id: str = "") -> tuple[bool, str]:
    """Check if hourly/daily limits are exceeded. Return (allowed, reason)."""
    cfg = load_guard_config()
    hourly_limit = cfg["hourly_usd_limit"]
    daily_limit = cfg["daily_usd_limit"]
    hourly_spend = spend_last_n_hours(1)
    daily_spend = spend_today()
    if hourly_spend >= hourly_limit:
        _write_guard_artifact(run_id, "hourly", hourly_spend, hourly_limit, daily_spend, daily_limit)
        return False, f"Hourly spend ${hourly_spend:.2f} >= ${hourly_limit:.2f} limit"
    if daily_spend >= daily_limit:
        _write_guard_artifact(run_id, "daily", hourly_spend, hourly_limit, daily_spend, daily_limit)
        return False, f"Daily spend ${daily_spend:.2f} >= ${daily_limit:.2f} limit"
    return True, ""


def _write_guard_artifact(
    run_id: str,
    tripped: str,
    hourly_spend: float,
    hourly_limit: float,
    daily_spend: float,
    daily_limit: float,
) -> None:
    """Write artifacts/cost_guard/<run_id>/summary.json, snapshot.json, reason.md."""
    if not run_id:
        run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_guard"
    d = _guard_dir() / run_id
    d.mkdir(parents=True, exist_ok=True)
    summary = {
        "tripped": tripped,
        "hourly_spend_usd": round(hourly_spend, 4),
        "hourly_limit_usd": hourly_limit,
        "daily_spend_usd": round(daily_spend, 4),
        "daily_limit_usd": daily_limit,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }
    (d / "summary.json").write_text(json.dumps(summary, indent=2))
    (d / "snapshot.json").write_text(json.dumps({
        "hourly_spend_usd": hourly_spend,
        "daily_spend_usd": daily_spend,
        "run_id": run_id,
    }, indent=2))
    reason = f"# Cost guard tripped\n\n**{tripped.upper()}** limit exceeded.\n\n"
    reason += f"- Hourly: ${hourly_spend:.2f} / ${hourly_limit:.2f}\n"
    reason += f"- Daily: ${daily_spend:.2f} / ${daily_limit:.2f}\n"
    (d / "reason.md").write_text(reason)


def costs_summary() -> dict[str, Any]:
    """Summary for GET /api/costs/summary: today, MTD, by project, top actions/models, last_poll."""
    roll = rollups_by_day(7)
    by_day = roll["by_day"]
    days_7 = sum(by_day.values())
    by_project = roll["by_project"]
    by_action = roll["by_action"]
    by_model = roll["by_model"]
    top_project = max(by_project.items(), key=lambda x: x[1], default=("", 0.0))
    return {
        "today_usd": round(spend_today(), 4),
        "mtd_usd": round(spend_mtd(), 4),
        "last_7_days_usd": round(days_7, 4),
        "top_project": {"id": top_project[0], "usd": round(top_project[1], 4)},
        "by_project": {k: round(v, 4) for k, v in sorted(by_project.items(), key=lambda x: -x[1])[:10]},
        "by_action": {k: round(v, 4) for k, v in sorted(by_action.items(), key=lambda x: -x[1])[:10]},
        "by_model": {k: round(v, 4) for k, v in sorted(by_model.items(), key=lambda x: -x[1])[:10]},
        "last_poll_time": None,
    }


def costs_timeseries(days: int = 30) -> dict[str, Any]:
    """Time series for GET /api/costs/timeseries?days=N."""
    roll = rollups_by_day(days)
    by_day = roll["by_day"]
    sorted_days = sorted(by_day.keys())
    return {
        "days": days,
        "series": [{"date": d, "usd": round(by_day[d], 4)} for d in sorted_days],
    }
