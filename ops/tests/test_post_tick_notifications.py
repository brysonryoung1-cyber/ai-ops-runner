from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ops.lib.human_gate import write_gate, write_gate_artifact
from ops.system.post_tick_notifications import (
    MAX_EVENT_RECORD_BYTES,
    append_event_log,
    build_transition_state_hash,
    process_post_tick_notifications,
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n",
        encoding="utf-8",
    )


def test_transition_hashing_is_stable(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENCLAW_ARTIFACTS_ROOT", str(tmp_path / "artifacts"))

    tick_a = {
        "run_id": "tick_1",
        "started_at": "2026-03-07T10:30:00Z",
        "finished_at": "2026-03-07T10:30:04Z",
        "autonomy_mode": "OFF",
        "observe_only": True,
        "projects_considered": 4,
        "decisions_written": 6,
        "executed_written": 2,
        "mutating_candidates_blocked": 3,
        "latest_paths": {
            "decisions": "artifacts/system/autonomy_scheduler/LATEST_decisions.jsonl",
            "executed": "artifacts/system/autonomy_scheduler/LATEST_executed.jsonl",
        },
    }
    tick_b = {
        "observe_only": True,
        "mutating_candidates_blocked": 3,
        "projects_considered": 4,
        "decisions_written": 6,
        "executed_written": 2,
        "finished_at": "2026-03-07T10:30:04Z",
        "started_at": "2026-03-07T10:30:00Z",
        "run_id": "tick_1",
        "autonomy_mode": "OFF",
        "latest_paths": {
            "executed": "artifacts/system/autonomy_scheduler/LATEST_executed.jsonl",
            "decisions": "artifacts/system/autonomy_scheduler/LATEST_decisions.jsonl",
        },
    }

    hash_a = build_transition_state_hash(
        tick_summary=tick_a,
        canary_core_status="FAIL",
        canary_optional_status="WARN",
        gate_status="OPEN",
        approvals_pending=2,
    )
    hash_b = build_transition_state_hash(
        tick_summary=tick_b,
        canary_core_status="FAIL",
        canary_optional_status="WARN",
        gate_status="OPEN",
        approvals_pending=2,
    )

    assert hash_a == hash_b


def test_event_log_append_caps_record_size(tmp_path: Path) -> None:
    artifacts_root = tmp_path / "artifacts"
    path = append_event_log(
        artifacts_root,
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "project_id": "soma_kajabi",
            "event_type": "CORE_DEGRADED",
            "state_hash": "hash",
            "summary": "x" * 2000,
            "proof_path": "artifacts/system/playbook_runs/" + ("p" * 600),
            "hq_path": "/projects/soma_kajabi",
            "tick_run_id": "tick_1",
            "alert": {
                "status": "ERROR",
                "deduped": False,
                "error_class": "DISCORD_HTTP_ERROR",
                "message": "y" * 2000,
            },
            "details": {
                "canary_core_status": "FAIL",
                "canary_optional_status": "WARN",
                "gate_status": "OPEN",
                "approvals_pending": 99,
                "playbook_id": "playbook." + ("z" * 400),
                "playbook_result_status": "FAILURE",
                "failed_checks": ["novnc_audit_failed"] * 100,
            },
        },
    )

    line = path.read_text(encoding="utf-8").strip()
    payload = json.loads(line)

    assert len(line.encode("utf-8")) <= MAX_EVENT_RECORD_BYTES
    assert len(payload["summary"]) < 500
    assert len(payload["alert"]["message"]) < 500
    assert len(payload["details"]["failed_checks"]) <= 8


def test_post_tick_notifications_writes_transition_and_event_artifacts(
    tmp_path: Path, monkeypatch
) -> None:
    artifacts_root = tmp_path / "artifacts"
    state_root = tmp_path / "state"
    sent_messages: list[str] = []

    monkeypatch.setenv("OPENCLAW_ARTIFACTS_ROOT", str(artifacts_root))
    monkeypatch.setenv("OPENCLAW_REPO_ROOT", str(tmp_path))
    monkeypatch.setenv("OPENCLAW_STATE_ROOT", str(state_root))
    monkeypatch.setenv("OPENCLAW_DISCORD_WEBHOOK_URL", "https://discord.invalid/super-secret-token")
    monkeypatch.setattr(
        "ops.lib.notification_router.send_discord_webhook_alert",
        lambda *, content, timeout_sec=10: sent_messages.append(content) or {"ok": True, "status_code": 204},
    )

    tick_summary = {
        "run_id": "tick_20260307T103000Z",
        "started_at": "2026-03-07T10:30:00.000Z",
        "finished_at": "2026-03-07T10:30:04.000Z",
        "autonomy_mode": "OFF",
        "observe_only": True,
        "projects_considered": 1,
        "decisions_written": 1,
        "executed_written": 1,
        "mutating_candidates_blocked": 0,
        "latest_paths": {
            "decisions": "artifacts/system/autonomy_scheduler/LATEST_decisions.jsonl",
            "executed": "artifacts/system/autonomy_scheduler/LATEST_executed.jsonl",
        },
    }
    _write_json(artifacts_root / "system" / "autonomy_scheduler" / "LATEST_tick_summary.json", tick_summary)
    _write_jsonl(
        artifacts_root / "system" / "autonomy_scheduler" / "LATEST_decisions.jsonl",
        [
            {
                "ts": "2026-03-07T10:30:01.000Z",
                "tick_run_id": "tick_20260307T103000Z",
                "project_id": "soma_kajabi",
                "playbook_id": "soma.resume_publish",
                "proof_bundle": "artifacts/system/playbook_runs/playbook_20260307T103001Z",
            }
        ],
    )
    _write_jsonl(
        artifacts_root / "system" / "autonomy_scheduler" / "LATEST_executed.jsonl",
        [
            {
                "ts": "2026-03-07T10:30:02.000Z",
                "tick_run_id": "tick_20260307T103000Z",
                "project_id": "soma_kajabi",
                "playbook_id": "soma.resume_publish",
                "result_status": "FAILURE",
                "proof_bundle": "artifacts/system/playbook_runs/playbook_20260307T103001Z",
            }
        ],
    )
    _write_json(
        artifacts_root / "system" / "canary" / "canary_20260307T102900Z" / "result.json",
        {
            "status": "DEGRADED",
            "core_status": "FAIL",
            "optional_status": "WARN",
            "core_failed_checks": ["novnc_audit_failed"],
            "proof": "artifacts/system/canary/canary_20260307T102900Z/PROOF.md",
        },
    )
    _write_json(
        artifacts_root / "system" / "approvals" / "approval_20260307T101901Z" / "request.json",
        {
            "id": "approval_20260307T101901Z",
            "project_id": "soma_kajabi",
            "status": "PENDING",
            "proof_bundle": "artifacts/system/playbook_runs/playbook_20260307T101900Z",
        },
    )

    gate = write_gate(
        "soma_kajabi",
        "run_gate_1",
        "https://novnc.example.invalid/session",
        "cloudflare",
    )
    write_gate_artifact("soma_kajabi", "run_gate_1", gate)

    result = process_post_tick_notifications(artifacts_root=artifacts_root)

    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    transition_path = artifacts_root / "system" / "transitions" / "soma_kajabi.json"
    event_log_path = artifacts_root / "system" / "events" / day / "events.jsonl"
    transition = json.loads(transition_path.read_text(encoding="utf-8"))
    event_rows = [
        json.loads(line)
        for line in event_log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert result["ok"] is True
    assert transition["last_hash"]
    assert transition["last_snapshot"]["approvals_pending"] == 1
    assert {row["event_type"] for row in event_rows} >= {
        "CORE_DEGRADED",
        "HUMAN_ONLY_OPEN",
        "APPROVAL_CREATED",
        "PLAYBOOK_FAIL",
    }
    assert any("proof_path:" in message for message in sent_messages)
    assert all("super-secret-token" not in message for message in sent_messages)
