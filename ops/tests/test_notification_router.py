from __future__ import annotations

import json
from pathlib import Path

from ops.lib.notification_router import (
    build_state_hash,
    read_transition_store,
    send_transition_notification,
)


def test_transition_notification_dedupes_by_project_event_and_state_hash(
    tmp_path: Path, monkeypatch
) -> None:
    sent_messages: list[str] = []

    monkeypatch.setenv("OPENCLAW_ARTIFACTS_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setattr(
        "ops.lib.notification_router.send_discord_webhook_alert",
        lambda *, content, timeout_sec=10: sent_messages.append(content) or {"ok": True, "http_code": 204},
    )

    state_hash = build_state_hash({"status": "FAIL", "check": "CORE.HQ"})

    first = send_transition_notification(
        project_id="infra_openclaw",
        event_type="CORE_DEGRADED",
        state_hash=state_hash,
        summary="Core degraded",
        proof_path="artifacts/system/canary/run_1",
        hq_path="/inbox",
    )
    second = send_transition_notification(
        project_id="infra_openclaw",
        event_type="CORE_DEGRADED",
        state_hash=state_hash,
        summary="Core degraded",
        proof_path="artifacts/system/canary/run_1",
        hq_path="/inbox",
    )
    different_event = send_transition_notification(
        project_id="infra_openclaw",
        event_type="CORE_RECOVERED",
        state_hash=state_hash,
        summary="Core recovered",
        proof_path="artifacts/system/canary/run_2",
        hq_path="/inbox",
    )

    store = read_transition_store("infra_openclaw")

    assert first["ok"] is True
    assert first["deduped"] is False
    assert second["deduped"] is True
    assert second["status"] == "DEDUPED"
    assert different_event["deduped"] is False
    assert store["last_hash"] == state_hash
    assert store["last_sent_events"]["CORE_DEGRADED"] == state_hash
    assert store["last_sent_events"]["CORE_RECOVERED"] == state_hash
    assert len(sent_messages) == 2


def test_notification_payload_includes_proof_path_and_not_secret_url(
    tmp_path: Path, monkeypatch
) -> None:
    sent_messages: list[str] = []

    monkeypatch.setenv("OPENCLAW_ARTIFACTS_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("OPENCLAW_DISCORD_WEBHOOK_URL", "https://discord.invalid/secrets/live-webhook-token")
    monkeypatch.setattr(
        "ops.lib.notification_router.send_discord_webhook_alert",
        lambda *, content, timeout_sec=10: sent_messages.append(content) or {"ok": True},
    )

    send_transition_notification(
        project_id="soma_kajabi",
        event_type="PLAYBOOK_FAIL",
        state_hash=build_state_hash({"tick": "1"}),
        summary="Playbook soma.resume_publish failed",
        proof_path="artifacts/system/playbook_runs/playbook_1",
        hq_path="/projects/soma_kajabi",
    )

    assert len(sent_messages) == 1
    assert "proof_path: artifacts/system/playbook_runs/playbook_1" in sent_messages[0]
    assert "https://discord.invalid/secrets/live-webhook-token" not in sent_messages[0]

    transition_path = tmp_path / "artifacts" / "system" / "transitions" / "soma_kajabi.json"
    payload = json.loads(transition_path.read_text(encoding="utf-8"))
    assert payload["project_id"] == "soma_kajabi"
