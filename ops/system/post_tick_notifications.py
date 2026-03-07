#!/usr/bin/env python3
"""Post-tick transition detection and best-effort notification routing."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ops.lib.artifacts_root import get_artifacts_root
from ops.lib.human_gate import read_gate
from ops.lib.notification_router import (
    build_state_hash,
    read_transition_store,
    send_transition_notification,
    write_transition_store,
)

EVENT_CORE_DEGRADED = "CORE_DEGRADED"
EVENT_CORE_RECOVERED = "CORE_RECOVERED"
EVENT_HUMAN_ONLY_OPEN = "HUMAN_ONLY_OPEN"
EVENT_HUMAN_ONLY_CLEARED = "HUMAN_ONLY_CLEARED"
EVENT_APPROVAL_CREATED = "APPROVAL_CREATED"
EVENT_APPROVAL_RESOLVED = "APPROVAL_RESOLVED"
EVENT_PLAYBOOK_PASS = "PLAYBOOK_PASS"
EVENT_PLAYBOOK_FAIL = "PLAYBOOK_FAIL"

PLAYBOOK_PASS_STATUSES = {"SUCCESS", "PASS", "REVIEW_READY", "JOINED_EXISTING_RUN"}
PLAYBOOK_FAIL_STATUSES = {"FAIL", "FAILURE", "ERROR", "BLOCKED", "TIMEOUT"}
MAX_EVENT_RECORD_BYTES = 2048
MAX_SUMMARY_LEN = 240
MAX_DETAIL_LEN = 240
MAX_FAILED_CHECKS = 8


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _repo_root() -> Path:
    env_root = os.environ.get("OPENCLAW_REPO_ROOT", "").strip()
    if env_root:
        return Path(env_root).expanduser()
    return Path(__file__).resolve().parents[2]


def resolve_artifacts_root(override: str | None = None) -> Path:
    if override:
        return Path(override).expanduser()
    return get_artifacts_root(repo_root=_repo_root().resolve())


def _state_root() -> Path:
    return Path(os.environ.get("OPENCLAW_STATE_ROOT", "/opt/ai-ops-runner/state")).expanduser()


def _bounded_text(value: Any, *, max_len: int = MAX_DETAIL_LEN) -> str:
    text = str(value or "").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 3].rstrip() + "..."


def _bounded_list(values: Any, *, max_items: int = MAX_FAILED_CHECKS) -> list[str]:
    if not isinstance(values, list):
        return []
    out: list[str] = []
    for value in values:
        text = _bounded_text(value, max_len=80)
        if text:
            out.append(text)
        if len(out) >= max_items:
            break
    return out


def _normalize_status(value: Any, *, allowed: set[str], default: str) -> str:
    text = str(value or "").strip().upper()
    return text if text in allowed else default


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            text = line.strip()
            if not text:
                continue
            payload = json.loads(text)
            if isinstance(payload, dict):
                out.append(payload)
    except (OSError, json.JSONDecodeError):
        return []
    return out


def _resolve_artifact_path(path_like: str | Path | None, artifacts_root: Path) -> Path | None:
    if not path_like:
        return None
    path = Path(path_like).expanduser()
    if path.is_absolute():
        return path
    text = str(path_like)
    if text.startswith("artifacts/"):
        return artifacts_root.parent / text
    return artifacts_root / text


def _artifact_relative(path_like: str | Path | None, artifacts_root: Path) -> str | None:
    if not path_like:
        return None
    text = str(path_like)
    if text.startswith("artifacts/"):
        return _bounded_text(text, max_len=320)
    path = Path(text).expanduser()
    if path.is_absolute():
        try:
            return _bounded_text(f"artifacts/{path.resolve().relative_to(artifacts_root.resolve())}", max_len=320)
        except ValueError:
            return _bounded_text(text, max_len=320)
    return _bounded_text(text, max_len=320)


def _latest_canary(artifacts_root: Path) -> dict[str, Any]:
    canary_root = artifacts_root / "system" / "canary"
    if not canary_root.exists():
        return {
            "core_status": "UNKNOWN",
            "optional_status": "UNKNOWN",
            "failed_checks": [],
            "proof_path": None,
        }
    for run_dir in sorted([item for item in canary_root.iterdir() if item.is_dir()], reverse=True):
        result = _read_json(run_dir / "result.json")
        if not result:
            continue
        proof = result.get("proof") or str(run_dir / "PROOF.md")
        return {
            "core_status": _normalize_status(
                result.get("core_status"),
                allowed={"PASS", "FAIL"},
                default="UNKNOWN",
            ),
            "optional_status": _normalize_status(
                result.get("optional_status"),
                allowed={"PASS", "WARN"},
                default="UNKNOWN",
            ),
            "failed_checks": _bounded_list(result.get("core_failed_checks") or []),
            "proof_path": _artifact_relative(proof, artifacts_root),
        }
    return {
        "core_status": "UNKNOWN",
        "optional_status": "UNKNOWN",
        "failed_checks": [],
        "proof_path": None,
    }


def _load_approvals(artifacts_root: Path) -> dict[str, dict[str, Any]]:
    approvals_root = artifacts_root / "system" / "approvals"
    out: dict[str, dict[str, Any]] = {}
    if not approvals_root.exists():
        return out
    for approval_dir in sorted([item for item in approvals_root.iterdir() if item.is_dir()], reverse=True):
        request = _read_json(approval_dir / "request.json")
        if not request:
            continue
        project_id = str(request.get("project_id") or "").strip()
        if not project_id:
            continue
        resolution = _read_json(approval_dir / "resolution.json")
        status = _normalize_status(
            (resolution or {}).get("status") or request.get("status"),
            allowed={"PENDING", "APPROVED", "REJECTED", "RESOLVED"},
            default="PENDING",
        )
        project = out.setdefault(
            project_id,
            {
                "pending_count": 0,
                "proof_path": None,
                "approval_ids": [],
            },
        )
        if status == "PENDING":
            project["pending_count"] += 1
        if len(project["approval_ids"]) < 5:
            project["approval_ids"].append(str(request.get("id") or approval_dir.name))
        if project["proof_path"] is None:
            project["proof_path"] = _artifact_relative(
                request.get("proof_bundle")
                or request.get("proof_bundle_url")
                or request.get("request_path")
                or (approval_dir / "request.json"),
                artifacts_root,
            )
    return out


def _discover_gate_projects() -> set[str]:
    gate_dir = _state_root() / "human_gate"
    if not gate_dir.exists():
        return set()
    return {
        path.stem
        for path in gate_dir.glob("*.json")
        if path.is_file() and path.stem.strip()
    }


def _latest_gate_proof_path(artifacts_root: Path, project_id: str, run_id: str | None = None) -> str | None:
    if run_id:
        return _artifact_relative(
            artifacts_root / project_id / "human_gate" / run_id / "HUMAN_GATE.json",
            artifacts_root,
        )
    gate_root = artifacts_root / project_id / "human_gate"
    if not gate_root.exists():
        return None
    for run_dir in sorted([item for item in gate_root.iterdir() if item.is_dir()], reverse=True):
        artifact = run_dir / "HUMAN_GATE.json"
        if artifact.exists():
            return _artifact_relative(artifact, artifacts_root)
    return None


def _normalize_tick_summary(payload: dict[str, Any]) -> dict[str, Any]:
    latest_paths = payload.get("latest_paths") if isinstance(payload.get("latest_paths"), dict) else {}
    return {
        "run_id": _bounded_text(payload.get("run_id"), max_len=96),
        "started_at": _bounded_text(payload.get("started_at"), max_len=48),
        "finished_at": _bounded_text(payload.get("finished_at"), max_len=48),
        "autonomy_mode": _bounded_text(payload.get("autonomy_mode"), max_len=16),
        "observe_only": bool(payload.get("observe_only")),
        "projects_considered": int(payload.get("projects_considered") or 0),
        "decisions_written": int(payload.get("decisions_written") or 0),
        "executed_written": int(payload.get("executed_written") or 0),
        "mutating_candidates_blocked": int(payload.get("mutating_candidates_blocked") or 0),
        "latest_paths": {
            str(key): _bounded_text(value, max_len=160)
            for key, value in latest_paths.items()
            if str(key).strip() and str(value).strip()
        },
    }


def build_transition_state_payload(
    *,
    tick_summary: dict[str, Any],
    canary_core_status: str,
    canary_optional_status: str,
    gate_status: str,
    approvals_pending: int,
) -> dict[str, Any]:
    return {
        "tick_summary": _normalize_tick_summary(tick_summary),
        "canary_core_status": _normalize_status(
            canary_core_status,
            allowed={"PASS", "FAIL", "UNKNOWN"},
            default="UNKNOWN",
        ),
        "canary_optional_status": _normalize_status(
            canary_optional_status,
            allowed={"PASS", "WARN", "UNKNOWN"},
            default="UNKNOWN",
        ),
        "gate_status": "OPEN" if str(gate_status).upper() == "OPEN" else "CLEARED",
        "approvals_pending": int(approvals_pending or 0),
    }


def build_transition_state_hash(
    *,
    tick_summary: dict[str, Any],
    canary_core_status: str,
    canary_optional_status: str,
    gate_status: str,
    approvals_pending: int,
) -> str:
    return build_state_hash(
        build_transition_state_payload(
            tick_summary=tick_summary,
            canary_core_status=canary_core_status,
            canary_optional_status=canary_optional_status,
            gate_status=gate_status,
            approvals_pending=approvals_pending,
        )
    )


def _resolve_latest_path(
    tick_summary: dict[str, Any],
    *,
    key: str,
    artifacts_root: Path,
    fallback: Path,
) -> Path:
    latest_paths = tick_summary.get("latest_paths") if isinstance(tick_summary.get("latest_paths"), dict) else {}
    ref = latest_paths.get(key)
    resolved = _resolve_artifact_path(ref, artifacts_root) if ref else None
    return resolved or fallback


def _records_by_project(records: list[dict[str, Any]], tick_run_id: str | None = None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for record in records:
        record_tick = str(record.get("tick_run_id") or "").strip()
        if tick_run_id and record_tick and record_tick != tick_run_id:
            continue
        project_id = str(record.get("project_id") or "").strip()
        if not project_id:
            continue
        out[project_id] = record
    return out


def _playbook_status(record: dict[str, Any] | None) -> tuple[str | None, str | None]:
    if not record:
        return None, None
    result_status = _normalize_status(
        record.get("result_status") or record.get("status"),
        allowed=PLAYBOOK_PASS_STATUSES | PLAYBOOK_FAIL_STATUSES | {"RUNNING", "APPROVAL_REQUIRED"},
        default="",
    )
    if result_status in PLAYBOOK_PASS_STATUSES:
        return "PASS", result_status
    if result_status in PLAYBOOK_FAIL_STATUSES:
        return "FAIL", result_status
    return None, result_status or None


def _project_snapshot(
    *,
    project_id: str,
    tick_summary: dict[str, Any],
    canary: dict[str, Any],
    approvals: dict[str, dict[str, Any]],
    executed_record: dict[str, Any] | None,
    decision_record: dict[str, Any] | None,
    artifacts_root: Path,
) -> dict[str, Any]:
    gate_info = read_gate(project_id)
    gate = gate_info.get("gate") if isinstance(gate_info.get("gate"), dict) else {}
    gate_status = "OPEN" if gate_info.get("active") else "CLEARED"
    playbook_event_status, playbook_result_status = _playbook_status(executed_record)
    approvals_state = approvals.get(project_id) or {}
    playbook_proof = None
    if executed_record:
        playbook_proof = executed_record.get("proof_bundle") or executed_record.get("proof_path")
    elif decision_record:
        playbook_proof = decision_record.get("proof_bundle") or decision_record.get("proof_path")
    return {
        "project_id": project_id,
        "tick_run_id": _bounded_text(tick_summary.get("run_id"), max_len=96),
        "canary_core_status": canary.get("core_status") or "UNKNOWN",
        "canary_optional_status": canary.get("optional_status") or "UNKNOWN",
        "canary_failed_checks": _bounded_list(canary.get("failed_checks") or []),
        "canary_proof_path": canary.get("proof_path"),
        "gate_status": gate_status,
        "gate_run_id": _bounded_text(gate.get("run_id"), max_len=96),
        "gate_reason": _bounded_text(gate.get("reason"), max_len=120),
        "gate_proof_path": _latest_gate_proof_path(artifacts_root, project_id, gate.get("run_id")),
        "approvals_pending": int(approvals_state.get("pending_count") or 0),
        "approval_ids": approvals_state.get("approval_ids") or [],
        "approval_proof_path": approvals_state.get("proof_path"),
        "playbook_event_status": playbook_event_status,
        "playbook_id": _bounded_text(
            (executed_record or {}).get("playbook_id") or (decision_record or {}).get("playbook_id"),
            max_len=120,
        ),
        "playbook_result_status": playbook_result_status,
        "playbook_proof_path": _artifact_relative(playbook_proof, artifacts_root),
        "hq_path": f"/projects/{quote(project_id)}",
        "state_hash": build_transition_state_hash(
            tick_summary=tick_summary,
            canary_core_status=str(canary.get("core_status") or "UNKNOWN"),
            canary_optional_status=str(canary.get("optional_status") or "UNKNOWN"),
            gate_status=gate_status,
            approvals_pending=int(approvals_state.get("pending_count") or 0),
        ),
    }


def _derive_events(previous: dict[str, Any] | None, current: dict[str, Any]) -> list[str]:
    events: list[str] = []
    prev_core = str((previous or {}).get("canary_core_status") or "UNKNOWN")
    curr_core = str(current.get("canary_core_status") or "UNKNOWN")
    prev_gate = str((previous or {}).get("gate_status") or "CLEARED")
    curr_gate = str(current.get("gate_status") or "CLEARED")
    prev_approvals = int((previous or {}).get("approvals_pending") or 0)
    curr_approvals = int(current.get("approvals_pending") or 0)
    prev_playbook = str((previous or {}).get("playbook_event_status") or "")
    curr_playbook = str(current.get("playbook_event_status") or "")

    if previous is None:
        if curr_core == "FAIL":
            events.append(EVENT_CORE_DEGRADED)
        if curr_gate == "OPEN":
            events.append(EVENT_HUMAN_ONLY_OPEN)
        if curr_approvals > 0:
            events.append(EVENT_APPROVAL_CREATED)
        if curr_playbook == "PASS":
            events.append(EVENT_PLAYBOOK_PASS)
        if curr_playbook == "FAIL":
            events.append(EVENT_PLAYBOOK_FAIL)
        return events

    if prev_core != "FAIL" and curr_core == "FAIL":
        events.append(EVENT_CORE_DEGRADED)
    elif prev_core == "FAIL" and curr_core == "PASS":
        events.append(EVENT_CORE_RECOVERED)

    if prev_gate != "OPEN" and curr_gate == "OPEN":
        events.append(EVENT_HUMAN_ONLY_OPEN)
    elif prev_gate == "OPEN" and curr_gate != "OPEN":
        events.append(EVENT_HUMAN_ONLY_CLEARED)

    if curr_approvals > prev_approvals:
        events.append(EVENT_APPROVAL_CREATED)
    elif curr_approvals < prev_approvals:
        events.append(EVENT_APPROVAL_RESOLVED)

    if curr_playbook == "PASS" and curr_playbook != prev_playbook:
        events.append(EVENT_PLAYBOOK_PASS)
    elif curr_playbook == "FAIL" and curr_playbook != prev_playbook:
        events.append(EVENT_PLAYBOOK_FAIL)

    return events


def _event_summary(event_type: str, snapshot: dict[str, Any], previous: dict[str, Any] | None) -> str:
    if event_type == EVENT_CORE_DEGRADED:
        checks = snapshot.get("canary_failed_checks") or []
        detail = f" Failed checks: {', '.join(checks)}." if checks else ""
        return _bounded_text(f"Core degraded for {snapshot['project_id']}.{detail}", max_len=MAX_SUMMARY_LEN)
    if event_type == EVENT_CORE_RECOVERED:
        optional = snapshot.get("canary_optional_status") or "UNKNOWN"
        return _bounded_text(
            f"Core recovered for {snapshot['project_id']}. Optional status: {optional}.",
            max_len=MAX_SUMMARY_LEN,
        )
    if event_type == EVENT_HUMAN_ONLY_OPEN:
        reason = snapshot.get("gate_reason") or "operator action required"
        return _bounded_text(
            f"Human gate opened for {snapshot['project_id']}: {reason}.",
            max_len=MAX_SUMMARY_LEN,
        )
    if event_type == EVENT_HUMAN_ONLY_CLEARED:
        return _bounded_text(f"Human gate cleared for {snapshot['project_id']}.", max_len=MAX_SUMMARY_LEN)
    if event_type == EVENT_APPROVAL_CREATED:
        prev_count = int((previous or {}).get("approvals_pending") or 0)
        curr_count = int(snapshot.get("approvals_pending") or 0)
        return _bounded_text(
            f"Pending approvals increased for {snapshot['project_id']}: {prev_count} -> {curr_count}.",
            max_len=MAX_SUMMARY_LEN,
        )
    if event_type == EVENT_APPROVAL_RESOLVED:
        prev_count = int((previous or {}).get("approvals_pending") or 0)
        curr_count = int(snapshot.get("approvals_pending") or 0)
        return _bounded_text(
            f"Pending approvals decreased for {snapshot['project_id']}: {prev_count} -> {curr_count}.",
            max_len=MAX_SUMMARY_LEN,
        )
    if event_type == EVENT_PLAYBOOK_PASS:
        return _bounded_text(
            f"Playbook {snapshot.get('playbook_id') or 'run'} passed with {snapshot.get('playbook_result_status') or 'SUCCESS'}.",
            max_len=MAX_SUMMARY_LEN,
        )
    if event_type == EVENT_PLAYBOOK_FAIL:
        return _bounded_text(
            f"Playbook {snapshot.get('playbook_id') or 'run'} failed with {snapshot.get('playbook_result_status') or 'FAIL'}.",
            max_len=MAX_SUMMARY_LEN,
        )
    return _bounded_text(f"{event_type} for {snapshot['project_id']}", max_len=MAX_SUMMARY_LEN)


def _event_proof_path(event_type: str, snapshot: dict[str, Any]) -> str | None:
    if event_type in {EVENT_CORE_DEGRADED, EVENT_CORE_RECOVERED}:
        return snapshot.get("canary_proof_path") or snapshot.get("playbook_proof_path")
    if event_type in {EVENT_HUMAN_ONLY_OPEN, EVENT_HUMAN_ONLY_CLEARED}:
        return snapshot.get("gate_proof_path") or snapshot.get("playbook_proof_path")
    if event_type in {EVENT_APPROVAL_CREATED, EVENT_APPROVAL_RESOLVED}:
        return snapshot.get("approval_proof_path") or snapshot.get("playbook_proof_path")
    if event_type in {EVENT_PLAYBOOK_PASS, EVENT_PLAYBOOK_FAIL}:
        return snapshot.get("playbook_proof_path") or snapshot.get("approval_proof_path")
    return None


def _bounded_event_record(record: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "ts": _bounded_text(record.get("ts"), max_len=48),
        "project_id": _bounded_text(record.get("project_id"), max_len=120),
        "event_type": _bounded_text(record.get("event_type"), max_len=64),
        "state_hash": _bounded_text(record.get("state_hash"), max_len=96),
        "summary": _bounded_text(record.get("summary"), max_len=MAX_SUMMARY_LEN),
        "proof_path": _bounded_text(record.get("proof_path"), max_len=320),
        "hq_path": _bounded_text(record.get("hq_path"), max_len=240),
        "tick_run_id": _bounded_text(record.get("tick_run_id"), max_len=96),
        "alert": {
            "status": _bounded_text(record.get("alert", {}).get("status"), max_len=16),
            "channel": "discord",
            "deduped": bool(record.get("alert", {}).get("deduped")),
            "error_class": _bounded_text(record.get("alert", {}).get("error_class"), max_len=64),
            "message": _bounded_text(record.get("alert", {}).get("message"), max_len=MAX_DETAIL_LEN),
        },
        "details": {
            "canary_core_status": _bounded_text(record.get("details", {}).get("canary_core_status"), max_len=16),
            "canary_optional_status": _bounded_text(
                record.get("details", {}).get("canary_optional_status"),
                max_len=16,
            ),
            "gate_status": _bounded_text(record.get("details", {}).get("gate_status"), max_len=16),
            "approvals_pending": int(record.get("details", {}).get("approvals_pending") or 0),
            "playbook_id": _bounded_text(record.get("details", {}).get("playbook_id"), max_len=120),
            "playbook_result_status": _bounded_text(
                record.get("details", {}).get("playbook_result_status"),
                max_len=32,
            ),
            "failed_checks": _bounded_list(record.get("details", {}).get("failed_checks") or []),
        },
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(raw) <= MAX_EVENT_RECORD_BYTES:
        return payload
    payload["details"] = {
        "canary_core_status": payload["details"]["canary_core_status"],
        "canary_optional_status": payload["details"]["canary_optional_status"],
        "gate_status": payload["details"]["gate_status"],
        "approvals_pending": payload["details"]["approvals_pending"],
        "truncated": True,
    }
    payload["alert"]["message"] = _bounded_text(payload["alert"]["message"], max_len=120)
    payload["summary"] = _bounded_text(payload["summary"], max_len=140)
    return payload


def append_event_log(artifacts_root: Path, record: dict[str, Any]) -> Path:
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = artifacts_root / "system" / "events" / day / "events.jsonl"
    payload = _bounded_event_record(record)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True))
        handle.write("\n")
    return path


def process_post_tick_notifications(
    *,
    artifacts_root: str | Path | None = None,
    tick_summary_path: str | Path | None = None,
    decisions_path: str | Path | None = None,
    executed_path: str | Path | None = None,
) -> dict[str, Any]:
    artifacts = resolve_artifacts_root(str(artifacts_root) if artifacts_root else None)
    tick_path = _resolve_artifact_path(
        tick_summary_path or artifacts / "system" / "autonomy_scheduler" / "LATEST_tick_summary.json",
        artifacts,
    )
    if tick_path is None:
        return {"ok": False, "status": "SKIP", "message": "tick_summary_path not set"}
    tick_summary = _read_json(tick_path)
    if not tick_summary:
        return {
            "ok": False,
            "status": "SKIP",
            "message": f"Missing or invalid tick summary: {tick_path}",
        }

    tick_run_id = str(tick_summary.get("run_id") or "").strip() or None
    decisions_file = _resolve_artifact_path(decisions_path, artifacts) if decisions_path else _resolve_latest_path(
        tick_summary,
        key="decisions",
        artifacts_root=artifacts,
        fallback=artifacts / "system" / "autonomy_scheduler" / "LATEST_decisions.jsonl",
    )
    executed_file = _resolve_artifact_path(executed_path, artifacts) if executed_path else _resolve_latest_path(
        tick_summary,
        key="executed",
        artifacts_root=artifacts,
        fallback=artifacts / "system" / "autonomy_scheduler" / "LATEST_executed.jsonl",
    )

    decisions = _read_jsonl(decisions_file)
    executed = _read_jsonl(executed_file)
    decisions_by_project = _records_by_project(decisions, tick_run_id=tick_run_id)
    executed_by_project = _records_by_project(executed, tick_run_id=tick_run_id)
    approvals = _load_approvals(artifacts)
    canary = _latest_canary(artifacts)

    project_ids = set(decisions_by_project) | set(executed_by_project) | set(approvals) | _discover_gate_projects()
    transitions_dir = artifacts / "system" / "transitions"
    if transitions_dir.exists():
        project_ids |= {path.stem for path in transitions_dir.glob("*.json") if path.is_file()}

    alerts: list[dict[str, Any]] = []
    event_logs: list[str] = []

    for project_id in sorted(project_id for project_id in project_ids if project_id.strip()):
        try:
            previous_store = read_transition_store(project_id)
            previous_snapshot = (
                previous_store.get("last_snapshot")
                if isinstance(previous_store.get("last_snapshot"), dict)
                else None
            )
            snapshot = _project_snapshot(
                project_id=project_id,
                tick_summary=tick_summary,
                canary=canary,
                approvals=approvals,
                executed_record=executed_by_project.get(project_id),
                decision_record=decisions_by_project.get(project_id),
                artifacts_root=artifacts,
            )
            store = {
                **previous_store,
                "project_id": project_id,
                "last_hash": snapshot["state_hash"],
                "last_snapshot": snapshot,
                "updated_at": now_utc(),
            }
            write_transition_store(project_id, store)

            for event_type in _derive_events(previous_snapshot, snapshot):
                summary = _event_summary(event_type, snapshot, previous_snapshot)
                proof_path = _event_proof_path(event_type, snapshot)
                notify = send_transition_notification(
                    project_id=project_id,
                    event_type=event_type,
                    state_hash=snapshot["state_hash"],
                    summary=summary,
                    proof_path=proof_path,
                    hq_path=str(snapshot.get("hq_path") or ""),
                )
                alert = {
                    "status": notify.get("status") or ("SENT" if notify.get("ok") else "ERROR"),
                    "deduped": bool(notify.get("deduped")),
                    "message": _bounded_text(notify.get("message"), max_len=MAX_DETAIL_LEN),
                    "error_class": _bounded_text(notify.get("error_class"), max_len=64),
                }
                log_path = append_event_log(
                    artifacts,
                    {
                        "ts": now_utc(),
                        "project_id": project_id,
                        "event_type": event_type,
                        "state_hash": snapshot["state_hash"],
                        "summary": summary,
                        "proof_path": proof_path,
                        "hq_path": snapshot.get("hq_path"),
                        "tick_run_id": snapshot.get("tick_run_id"),
                        "alert": alert,
                        "details": {
                            "canary_core_status": snapshot.get("canary_core_status"),
                            "canary_optional_status": snapshot.get("canary_optional_status"),
                            "gate_status": snapshot.get("gate_status"),
                            "approvals_pending": snapshot.get("approvals_pending"),
                            "playbook_id": snapshot.get("playbook_id"),
                            "playbook_result_status": snapshot.get("playbook_result_status"),
                            "failed_checks": snapshot.get("canary_failed_checks"),
                        },
                    },
                )
                event_logs.append(str(log_path))
                alerts.append(
                    {
                        "project_id": project_id,
                        "event_type": event_type,
                        "status": alert["status"],
                        "deduped": alert["deduped"],
                        "message": alert["message"],
                        "proof_path": proof_path,
                    }
                )
        except Exception as exc:  # noqa: BLE001
            alerts.append(
                {
                    "project_id": project_id,
                    "event_type": None,
                    "status": "ERROR",
                    "deduped": False,
                    "message": _bounded_text(str(exc) or type(exc).__name__, max_len=MAX_DETAIL_LEN),
                    "proof_path": None,
                }
            )

    return {
        "ok": True,
        "status": "OK",
        "tick_run_id": tick_run_id,
        "projects_considered": len(project_ids),
        "alerts": alerts,
        "event_logs": sorted(set(event_logs)),
        "tick_summary_path": str(tick_path),
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Process post-tick transition notifications.")
    parser.add_argument("--artifacts-root", default="")
    parser.add_argument("--tick-summary-path", default="")
    parser.add_argument("--decisions-path", default="")
    parser.add_argument("--executed-path", default="")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or [])
    result = process_post_tick_notifications(
        artifacts_root=args.artifacts_root or None,
        tick_summary_path=args.tick_summary_path or None,
        decisions_path=args.decisions_path or None,
        executed_path=args.executed_path or None,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main(os.sys.argv[1:]))
