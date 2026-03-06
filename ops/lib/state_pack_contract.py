from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_FRESHNESS_THRESHOLD_SEC = 2 * 60 * 60
SCHEMA_VERSION = 1
COMPLETION_MARKER_NAME = "LATEST.ok"
REQUIRED_PACK_FILES = (
    "health_public.json",
    "autopilot_status.json",
    "llm_status.json",
    "tailscale_serve.txt",
    "ports.txt",
    "systemd_openclaw-novnc.txt",
    "systemd_openclaw-frontdoor.txt",
    "systemd_openclaw-hostd.txt",
    "systemd_openclaw-guard.txt",
    "systemd_hq.txt",
    "latest_runs_index.json",
    "build_sha.txt",
    "novnc_http_check.json",
    "ws_probe.json",
    "SUMMARY.md",
    "RESULT.json",
)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _read_json_with_error(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, "missing"
    except json.JSONDecodeError:
        return None, "json_invalid"
    except OSError:
        return None, "os_error"


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


def _normalize_artifact_path(artifacts_root: Path, raw_path: Any) -> Path | None:
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None
    candidate = Path(raw_path.strip())
    if candidate.is_absolute():
        return candidate
    parts = candidate.parts
    if parts and parts[0] == "artifacts":
        return artifacts_root.joinpath(*parts[1:])
    return artifacts_root.joinpath(*parts)


def load_latest(path: Path | str) -> dict[str, Any]:
    latest_json = Path(path)
    payload, read_error = _read_json_with_error(latest_json)
    artifacts_root = latest_json.parents[2] if len(latest_json.parents) >= 3 else latest_json.parent
    payload = payload or {}
    latest_path = _normalize_artifact_path(
        artifacts_root,
        payload.get("latest_path") or payload.get("pack_dir") or payload.get("artifact_dir"),
    )
    result_path = _normalize_artifact_path(artifacts_root, payload.get("result_path"))
    if result_path is None and latest_path is not None:
        result_path = latest_path / "RESULT.json"
    finished_at = _parse_timestamp(
        payload.get("finished_at") or payload.get("generated_at") or payload.get("updated_at")
    )
    return {
        "latest_json": str(latest_json),
        "artifacts_root": str(artifacts_root),
        "exists": latest_json.exists(),
        "read_error": read_error,
        "payload": payload,
        "latest_path": str(latest_path) if latest_path is not None else None,
        "result_path": str(result_path) if result_path is not None else None,
        "run_id": payload.get("run_id"),
        "finished_at": finished_at.isoformat() if finished_at is not None else None,
        "sha": payload.get("sha"),
        "schema_version": payload.get("schema_version"),
        "status": payload.get("status"),
    }


def validate_latest(latest: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    latest_json = latest.get("latest_json")
    if not latest.get("exists"):
        return "FAIL", "LATEST_MISSING", {"latest_json": latest_json}

    read_error = latest.get("read_error")
    if read_error is not None:
        return "FAIL", "LATEST_JSON_INVALID", {"latest_json": latest_json, "read_error": read_error}

    actual_schema_version = latest.get("schema_version")
    if actual_schema_version != SCHEMA_VERSION:
        return (
            "FAIL",
            "LATEST_SCHEMA_MISMATCH",
            {
                "latest_json": latest_json,
                "expected_schema_version": SCHEMA_VERSION,
                "actual_schema_version": actual_schema_version,
            },
        )

    latest_path_raw = latest.get("latest_path")
    if not latest_path_raw:
        return "FAIL", "LATEST_PATH_MISSING", {"latest_json": latest_json, "latest_path": None}

    latest_path = Path(latest_path_raw)
    if not latest_path.exists():
        return "FAIL", "LATEST_PATH_MISSING", {"latest_json": latest_json, "latest_path": str(latest_path)}

    result_path = Path(latest.get("result_path") or latest_path / "RESULT.json")
    if not result_path.exists():
        return (
            "FAIL",
            "RESULT_JSON_MISSING",
            {"latest_path": str(latest_path), "result_path": str(result_path)},
        )

    result_payload, result_error = _read_json_with_error(result_path)
    if result_error is not None:
        return (
            "FAIL",
            "RESULT_NOT_PASS",
            {"latest_path": str(latest_path), "result_path": str(result_path), "result_error": result_error},
        )

    result_status = (result_payload or {}).get("status")
    if result_status != "PASS":
        return (
            "FAIL",
            "RESULT_NOT_PASS",
            {
                "latest_path": str(latest_path),
                "result_path": str(result_path),
                "result_status": result_status,
                "result_reason": (result_payload or {}).get("reason"),
            },
        )

    missing_files = [name for name in REQUIRED_PACK_FILES if not (latest_path / name).exists()]
    marker_path = latest_path / COMPLETION_MARKER_NAME
    if not marker_path.exists():
        missing_files.append(COMPLETION_MARKER_NAME)
    if missing_files:
        return (
            "FAIL",
            "PACK_INCOMPLETE",
            {
                "latest_path": str(latest_path),
                "missing_files": missing_files[:10],
                "missing_files_count": len(missing_files),
                "completion_marker": str(marker_path),
            },
        )

    return (
        "PASS",
        "LATEST_OK",
        {
            "latest_json": latest_json,
            "latest_path": str(latest_path),
            "result_path": str(result_path),
            "completion_marker": str(marker_path),
            "required_files_count": len(REQUIRED_PACK_FILES) + 1,
        },
    )


def evaluate_state_pack_integrity(artifacts_root: Path) -> dict[str, Any]:
    latest = load_latest(latest_contract_path(artifacts_root))
    status, reason, details = validate_latest(latest)
    return {
        "status": status,
        "reason": reason,
        "latest_json": latest.get("latest_json"),
        "latest_path": latest.get("latest_path"),
        "result_path": latest.get("result_path"),
        "run_id": latest.get("run_id"),
        "finished_at": latest.get("finished_at"),
        "sha": latest.get("sha"),
        "schema_version": latest.get("schema_version"),
        "details": details,
    }


def resolve_latest_state_pack(artifacts_root: Path, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    integrity = evaluate_state_pack_integrity(artifacts_root)
    latest = load_latest(latest_contract_path(artifacts_root))
    base: dict[str, Any] = {
        "status": "FAIL",
        "reason": integrity.get("reason"),
        "latest_json": integrity.get("latest_json"),
        "latest_path": integrity.get("latest_path"),
        "run_id": integrity.get("run_id"),
        "generated_at": None,
        "age_sec": None,
    }
    if integrity.get("status") != "PASS":
        return base

    latest_path_raw = latest.get("latest_path")
    generated_at = _parse_timestamp(latest.get("finished_at"))
    if generated_at is None:
        base["reason"] = "LATEST_TIMESTAMP_INVALID"
        base["latest_path"] = latest_path_raw
        base["run_id"] = latest.get("run_id")
        return base

    age_sec = max(0, int((now - generated_at).total_seconds()))
    result = {
        "status": "PASS",
        "reason": "LATEST_OK",
        "latest_json": integrity.get("latest_json"),
        "latest_path": latest_path_raw,
        "run_id": latest.get("run_id"),
        "generated_at": generated_at.isoformat(),
        "age_sec": age_sec,
    }
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
        result["reason"] = "LATEST_TOO_OLD"
        return result
    result["status"] = "PASS"
    result["reason"] = "STATE_PACK_FRESH"
    return result
