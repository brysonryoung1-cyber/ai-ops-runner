from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_FRESHNESS_THRESHOLD_SEC = 2 * 60 * 60


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _parse_timestamp(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def latest_contract_path(artifacts_root: Path) -> Path:
    return artifacts_root / "system" / "state_pack" / "LATEST.json"


def _normalize_latest_path(artifacts_root: Path, raw_path: Any) -> Path | None:
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None
    candidate = Path(raw_path.strip())
    if candidate.is_absolute():
        return candidate
    parts = candidate.parts
    if parts and parts[0] == "artifacts":
        return artifacts_root.joinpath(*parts[1:])
    return artifacts_root.joinpath(*parts)


def resolve_latest_state_pack(artifacts_root: Path, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    latest_json = latest_contract_path(artifacts_root)
    payload = _read_json(latest_json)
    base: dict[str, Any] = {
        "status": "FAIL",
        "reason": "LATEST_MISSING",
        "latest_json": str(latest_json),
        "latest_path": None,
        "run_id": None,
        "generated_at": None,
        "age_sec": None,
    }
    if payload is None:
        return base

    latest_path = _normalize_latest_path(
        artifacts_root,
        payload.get("latest_path") or payload.get("pack_dir") or payload.get("artifact_dir"),
    )
    if latest_path is None:
        base["reason"] = "LATEST_PATH_MISSING"
        return base

    generated_at = _parse_timestamp(
        payload.get("generated_at") or payload.get("finished_at") or payload.get("updated_at")
    )
    if generated_at is None:
        base["reason"] = "LATEST_TIMESTAMP_INVALID"
        base["latest_path"] = str(latest_path)
        base["run_id"] = payload.get("run_id")
        return base

    age_sec = max(0, int((now - generated_at).total_seconds()))
    result = {
        "status": "PASS",
        "reason": "LATEST_OK",
        "latest_json": str(latest_json),
        "latest_path": str(latest_path),
        "run_id": payload.get("run_id"),
        "generated_at": generated_at.isoformat(),
        "age_sec": age_sec,
    }
    if not latest_path.exists():
        result["status"] = "FAIL"
        result["reason"] = "LATEST_PATH_NOT_FOUND"
    return result


def evaluate_state_pack_freshness(
    artifacts_root: Path,
    threshold_sec: int = DEFAULT_FRESHNESS_THRESHOLD_SEC,
    now: datetime | None = None,
) -> dict[str, Any]:
    resolved = resolve_latest_state_pack(artifacts_root=artifacts_root, now=now)
    result = {
        "latest_json": resolved.get("latest_json"),
        "latest_path": resolved.get("latest_path"),
        "run_id": resolved.get("run_id"),
        "generated_at": resolved.get("generated_at"),
        "age_sec": resolved.get("age_sec"),
        "threshold_sec": int(threshold_sec),
        "status": "FAIL",
        "reason": resolved.get("reason"),
    }
    if resolved.get("status") != "PASS":
        return result
    age_sec = int(resolved.get("age_sec") or 0)
    if age_sec > int(threshold_sec):
        result["reason"] = "STATE_PACK_STALE"
        return result
    result["status"] = "PASS"
    result["reason"] = "STATE_PACK_FRESH"
    return result
