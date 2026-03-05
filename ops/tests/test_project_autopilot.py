from __future__ import annotations

import json
from pathlib import Path

from ops.system.project_autopilot import main


def _load_result(artifacts_root: Path, run_id: str) -> dict:
    result_path = artifacts_root / "system" / "project_autopilot" / run_id / "RESULT.json"
    assert result_path.is_file(), f"missing RESULT.json: {result_path}"
    return json.loads(result_path.read_text(encoding="utf-8"))


class _FakeDiscordResponse:
    def __init__(self, status_code: int = 204):
        self._status_code = status_code

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
        return False

    def getcode(self) -> int:
        return self._status_code


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
    assert result["run_artifact_dir_resolution"] == "listing"
    assert result["links"]["run_to_done_dir"].startswith("artifacts/soma_kajabi/run_to_done/")


def test_pointer_match_bypasses_listing_and_fetches_proof(tmp_path: Path) -> None:
    artifacts_root = tmp_path / "artifacts"
    state_root = tmp_path / "state"
    run_dir_name = "run_to_done_20260305T123000Z_ptr1111"
    remote_run_id = "20260305123000-abcd"
    spec_path = tmp_path / "mock_pointer_match.json"
    spec_path.write_text(
        json.dumps(
            {
                "pointer_payload": {
                    "console_run_id": remote_run_id,
                    "run_dir": run_dir_name,
                    "status": "SUCCESS",
                    "updated_at": "2026-03-05T12:30:00Z",
                    "error_class": None,
                },
                "run_to_done_entries": [run_dir_name],
                "proof_payload": {
                    "status": "SUCCESS",
                    "acceptance_path": "artifacts/soma_kajabi/acceptance/mock_pointer_match",
                },
                "precheck_payload": {"status": "PASS"},
            }
        ),
        encoding="utf-8",
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
            remote_run_id,
            "--run-id",
            "ap_pointer_match",
            "--state-root",
            str(state_root),
            "--artifacts-root",
            str(artifacts_root),
            "--validator-actions",
            "",
            "--mock-hq-file",
            str(spec_path),
        ]
    )

    assert rc == 0
    result = _load_result(artifacts_root, "ap_pointer_match")
    assert result["status"] == "SUCCESS"
    assert result["run_artifact_dir_resolution"] == "pointer"
    assert result["pointer_seen_console_run_id"] == remote_run_id
    assert result["pointer_seen_run_dir"] == run_dir_name
    assert result["poll"]["run_artifact_dir"] == f"artifacts/soma_kajabi/run_to_done/{run_dir_name}"
    assert result["links"]["proof_path"] == f"artifacts/soma_kajabi/run_to_done/{run_dir_name}/PROOF.json"

    raw = artifacts_root / "system" / "project_autopilot" / "ap_pointer_match" / "raw"
    assert (raw / "browse_run_to_done_pointer.json").is_file()
    assert not (raw / "browse_run_to_done_dirs.json").exists()


def test_pointer_mismatch_triggers_listing_fallback(tmp_path: Path) -> None:
    artifacts_root = tmp_path / "artifacts"
    state_root = tmp_path / "state"
    remote_run_id = "20260305124000-abcd"
    run_dir_name = "run_to_done_20260305T124000Z_list2222"
    spec_path = tmp_path / "mock_pointer_mismatch.json"
    spec_path.write_text(
        json.dumps(
            {
                "pointer_payload": {
                    "console_run_id": "20260305123959-zzzz",
                    "run_dir": "run_to_done_20260305T123959Z_ptr9999",
                    "status": "SUCCESS",
                    "updated_at": "2026-03-05T12:40:00Z",
                    "error_class": None,
                },
                "run_to_done_entries": [run_dir_name],
                "proof_payload": {
                    "status": "SUCCESS",
                    "acceptance_path": "artifacts/soma_kajabi/acceptance/mock_pointer_listing",
                },
                "precheck_payload": {"status": "PASS"},
            }
        ),
        encoding="utf-8",
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
            remote_run_id,
            "--run-id",
            "ap_pointer_mismatch",
            "--state-root",
            str(state_root),
            "--artifacts-root",
            str(artifacts_root),
            "--validator-actions",
            "",
            "--mock-hq-file",
            str(spec_path),
        ]
    )

    assert rc == 0
    result = _load_result(artifacts_root, "ap_pointer_mismatch")
    assert result["status"] == "SUCCESS"
    assert result["run_artifact_dir_resolution"] == "listing"
    assert result["pointer_seen_console_run_id"] == "20260305123959-zzzz"
    assert result["pointer_seen_run_dir"] == "run_to_done_20260305T123959Z_ptr9999"
    assert result["poll"]["run_artifact_dir"] == f"artifacts/soma_kajabi/run_to_done/{run_dir_name}"

    raw = artifacts_root / "system" / "project_autopilot" / "ap_pointer_mismatch" / "raw"
    assert (raw / "browse_run_to_done_pointer.json").is_file()
    assert (raw / "browse_run_to_done_dirs.json").is_file()


def test_mock_waiting_alert_uses_webhook_resolution_env_and_file(tmp_path: Path, monkeypatch) -> None:
    urls: list[str] = []

    def _fake_urlopen(req, timeout=10):  # noqa: ANN001,ARG001
        urls.append(req.full_url)
        return _FakeDiscordResponse(status_code=204)

    monkeypatch.setattr("ops.lib.notifier.request.urlopen", _fake_urlopen)

    env_webhook = "https://discord.example/env-webhook"
    monkeypatch.setenv("OPENCLAW_DISCORD_WEBHOOK_URL", env_webhook)
    rc_env = main(
        [
            "--project",
            "soma_kajabi",
            "--action",
            "soma_run_to_done",
            "--mock",
            "--mock-terminal-status",
            "WAITING_FOR_HUMAN",
            "--mock-run-id",
            "20260305130000-env1",
            "--run-id",
            "ap_alert_env",
            "--state-root",
            str(tmp_path / "state_env"),
            "--artifacts-root",
            str(tmp_path / "artifacts_env"),
            "--validator-actions",
            "",
        ]
    )
    assert rc_env == 0
    env_result = _load_result(tmp_path / "artifacts_env", "ap_alert_env")
    assert env_result["status"] == "WAITING_FOR_HUMAN"
    assert env_result["alert"]["needed"] is True
    assert env_result["alert"]["sent"] is True
    assert env_result["alert"]["notify"]["ok"] is True
    assert env_result["alert"]["notify"]["source"] == "env"
    assert urls[-1] == env_webhook

    monkeypatch.delenv("OPENCLAW_DISCORD_WEBHOOK_URL", raising=False)
    secret_path = tmp_path / "discord_webhook_url"
    secret_webhook = "https://discord.example/file-webhook"
    secret_path.write_text(secret_webhook + "\n", encoding="utf-8")
    monkeypatch.setattr("ops.lib.notifier.DISCORD_WEBHOOK_SECRET_FILE", secret_path)

    rc_file = main(
        [
            "--project",
            "soma_kajabi",
            "--action",
            "soma_run_to_done",
            "--mock",
            "--mock-terminal-status",
            "WAITING_FOR_HUMAN",
            "--mock-run-id",
            "20260305130100-fil1",
            "--run-id",
            "ap_alert_file",
            "--state-root",
            str(tmp_path / "state_file"),
            "--artifacts-root",
            str(tmp_path / "artifacts_file"),
            "--validator-actions",
            "",
        ]
    )
    assert rc_file == 0
    file_result = _load_result(tmp_path / "artifacts_file", "ap_alert_file")
    assert file_result["status"] == "WAITING_FOR_HUMAN"
    assert file_result["alert"]["needed"] is True
    assert file_result["alert"]["sent"] is True
    assert file_result["alert"]["notify"]["ok"] is True
    assert file_result["alert"]["notify"]["source"] == "file"
    assert urls[-1] == secret_webhook
