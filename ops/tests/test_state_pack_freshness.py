import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ops.lib.state_pack_contract import COMPLETION_MARKER_NAME, SCHEMA_VERSION, evaluate_state_pack_freshness

REPO_ROOT = Path(__file__).resolve().parents[2]


def _write_latest(artifacts_root: Path, generated_at: datetime) -> Path:
    run_dir = artifacts_root / "system" / "state_pack" / "state_pack_test_run"
    run_dir.mkdir(parents=True, exist_ok=True)
    for name in [
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
    ]:
        (run_dir / name).write_text("{}\n", encoding="utf-8")
    (run_dir / "RESULT.json").write_text(
        json.dumps({"status": "PASS", "reason": "state_pack_generated"}) + "\n",
        encoding="utf-8",
    )
    (run_dir / COMPLETION_MARKER_NAME).write_text('{"ok":true}\n', encoding="utf-8")
    latest_path = artifacts_root / "system" / "state_pack" / "LATEST.json"
    latest_path.parent.mkdir(parents=True, exist_ok=True)
    latest_path.write_text(
        json.dumps(
            {
                "status": "PASS",
                "reason": "state_pack_generated",
                "run_id": "state_pack_test_run",
                "generated_at": generated_at.isoformat(),
                "finished_at": generated_at.isoformat(),
                "latest_path": str(run_dir),
                "result_path": str(run_dir / "RESULT.json"),
                "schema_version": SCHEMA_VERSION,
                "sha": "abc1234",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return latest_path


def test_state_pack_freshness_fails_when_latest_is_stale(tmp_path: Path) -> None:
    artifacts_root = tmp_path / "artifacts"
    now = datetime.now(timezone.utc)
    _write_latest(artifacts_root, now - timedelta(hours=3))

    payload = evaluate_state_pack_freshness(
        artifacts_root=artifacts_root,
        threshold_sec=7200,
        now=now,
    )

    assert payload["status"] == "FAIL"
    assert payload["reason"] == "LATEST_TOO_OLD"
    assert payload["age_sec"] >= 10800
    assert payload["threshold_sec"] == 7200


def test_state_pack_freshness_passes_when_latest_is_fresh(tmp_path: Path) -> None:
    artifacts_root = tmp_path / "artifacts"
    now = datetime.now(timezone.utc)
    _write_latest(artifacts_root, now - timedelta(minutes=15))

    payload = evaluate_state_pack_freshness(
        artifacts_root=artifacts_root,
        threshold_sec=7200,
        now=now,
    )

    assert payload["status"] == "PASS"
    assert payload["reason"] == "STATE_PACK_FRESH"
    assert payload["latest_path"].endswith("state_pack_test_run")
    assert payload["age_sec"] < payload["threshold_sec"]


def test_doctor_wires_state_pack_freshness_artifact() -> None:
    doctor_script = REPO_ROOT / "ops" / "openclaw_doctor.sh"
    text = doctor_script.read_text(encoding="utf-8")
    assert "state_pack_freshness" in text
    assert "state_pack_integrity" in text
    assert "STATE_PACK_FRESHNESS_JSON" in text
    assert "STATE_PACK_INTEGRITY_STATUS_JSON" in text
    assert "threshold_sec" in text
