from __future__ import annotations

import json
from pathlib import Path

from ops.system.project_autopilot import main


def _load_result(artifacts_root: Path, run_id: str) -> dict:
    result_path = artifacts_root / "system" / "project_autopilot" / run_id / "RESULT.json"
    assert result_path.is_file(), f"missing RESULT.json: {result_path}"
    return json.loads(result_path.read_text(encoding="utf-8"))


def test_mock_waiting_writes_bundle_and_dedupes_alert(tmp_path: Path, monkeypatch) -> None:
    artifacts_root = tmp_path / "artifacts"
    state_root = tmp_path / "state"
    sent_messages: list[str] = []

    def _fake_notify(*, content: str, timeout_sec: int = 10):  # noqa: ARG001
        sent_messages.append(content)
        return {"ok": True, "http_code": 204}

    monkeypatch.setattr("ops.system.project_autopilot.send_discord_webhook_alert", _fake_notify)

    rc1 = main(
        [
            "--project",
            "soma_kajabi",
            "--action",
            "soma_run_to_done",
            "--mock",
            "--mock-terminal-status",
            "WAITING_FOR_HUMAN",
            "--mock-run-id",
            "20260305120000-abcd",
            "--run-id",
            "ap_wait_1",
            "--state-root",
            str(state_root),
            "--artifacts-root",
            str(artifacts_root),
            "--validator-actions",
            "",
        ]
    )
    assert rc1 == 0
    result1 = _load_result(artifacts_root, "ap_wait_1")
    assert result1["status"] == "WAITING_FOR_HUMAN"
    assert result1["links"]["proof_path"].endswith("/PROOF.json")
    assert result1["alert"]["sent"] is True
    assert len(sent_messages) == 1
    assert "run_id: `20260305120000-abcd`" in sent_messages[0]
    assert "proof_path:" in sent_messages[0]
    assert "novnc_url:" in sent_messages[0]
    lower = sent_messages[0].lower()
    assert "token" not in lower
    assert "webhook" not in lower
    assert "secret" not in lower

    # Same remote run_id + status/error tuple should dedupe.
    rc2 = main(
        [
            "--project",
            "soma_kajabi",
            "--action",
            "soma_run_to_done",
            "--mock",
            "--mock-terminal-status",
            "WAITING_FOR_HUMAN",
            "--mock-run-id",
            "20260305120000-abcd",
            "--run-id",
            "ap_wait_2",
            "--state-root",
            str(state_root),
            "--artifacts-root",
            str(artifacts_root),
            "--validator-actions",
            "",
        ]
    )
    assert rc2 == 0
    result2 = _load_result(artifacts_root, "ap_wait_2")
    assert result2["status"] == "WAITING_FOR_HUMAN"
    assert result2["alert"]["deduped"] is True
    assert result2["alert"]["sent"] is False
    assert len(sent_messages) == 1


def test_mock_doctor_fail_exits_nonzero_and_writes_result(tmp_path: Path, monkeypatch) -> None:
    artifacts_root = tmp_path / "artifacts"
    state_root = tmp_path / "state"

    monkeypatch.setattr(
        "ops.system.project_autopilot.send_discord_webhook_alert",
        lambda **_kwargs: {"ok": True, "http_code": 204},
    )

    rc = main(
        [
            "--project",
            "soma_kajabi",
            "--action",
            "soma_run_to_done",
            "--mock",
            "--mock-doctor-status",
            "FAIL",
            "--run-id",
            "ap_doctor_fail",
            "--state-root",
            str(state_root),
            "--artifacts-root",
            str(artifacts_root),
        ]
    )
    assert rc == 1
    result = _load_result(artifacts_root, "ap_doctor_fail")
    assert result["status"] == "FAIL"
    assert result["error_class"] == "DOCTOR_MATRIX_FAIL"
    assert result["doctor"]["status"] == "FAIL"


def test_mock_success_writes_proof_bundle(tmp_path: Path, monkeypatch) -> None:
    artifacts_root = tmp_path / "artifacts"
    state_root = tmp_path / "state"

    monkeypatch.setattr(
        "ops.system.project_autopilot.send_discord_webhook_alert",
        lambda **_kwargs: {"ok": True, "http_code": 204},
    )

    rc = main(
        [
            "--project",
            "soma_kajabi",
            "--action",
            "soma_run_to_done",
            "--mock",
            "--mock-terminal-status",
            "SUCCESS",
            "--mock-run-id",
            "20260305122000-abcd",
            "--run-id",
            "ap_success",
            "--state-root",
            str(state_root),
            "--artifacts-root",
            str(artifacts_root),
            "--validator-actions",
            "",
        ]
    )
    assert rc == 0

    bundle = artifacts_root / "system" / "project_autopilot" / "ap_success"
    assert (bundle / "RESULT.json").is_file()
    assert (bundle / "SUMMARY.md").is_file()
    assert (bundle / "run_to_done_PROOF.json").is_file()
    raw = bundle / "raw"
    assert (raw / "poll_001.json").is_file()
    poll_files = sorted(raw.glob("poll_*.json"))
    assert poll_files
    assert (raw / "browse_run_to_done_dirs.json").is_file()

    result = _load_result(artifacts_root, "ap_success")
    assert result["status"] == "SUCCESS"
    assert result["alert"]["needed"] is False
    assert result["links"]["run_to_done_dir"].startswith("artifacts/soma_kajabi/run_to_done/")
