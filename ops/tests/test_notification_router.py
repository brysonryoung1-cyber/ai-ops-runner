from __future__ import annotations

from pathlib import Path

from ops.lib.notification_router import build_state_hash, send_transition_notification


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

    assert first["ok"] is True
    assert first["deduped"] is False
    assert second["deduped"] is True
    assert different_event["deduped"] is False
    assert len(sent_messages) == 2
