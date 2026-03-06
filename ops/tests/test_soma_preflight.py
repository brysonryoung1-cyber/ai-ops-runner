from __future__ import annotations

import json
from pathlib import Path

from ops.system import soma_preflight as sp


def _record(status: str) -> sp.CheckRecord:
    return sp.CheckRecord(status=status, details={"mock": True})


def _base_active() -> dict[str, object]:
    return {
        "active_status": "idle",
        "active_run_id": None,
        "novnc_url": None,
        "gate_expiry": None,
        "status_endpoint_http_code": 200,
        "status_payload": {"ok": True},
        "lock_run_http_code": 200,
        "lock_run_payload": {"locked": False},
        "lock_auto_http_code": 200,
        "lock_auto_payload": {"locked": False},
    }


def _patch_default_checks(monkeypatch, *, artifacts_root: Path) -> None:
    monkeypatch.setattr(sp, "_check_health_public", lambda _hq: _record("PASS"))
    monkeypatch.setattr(sp, "_check_host_executor_reachable", lambda _hq: _record("PASS"))
    monkeypatch.setattr(sp, "_check_ws_paths", lambda: (_record("PASS"), _record("PASS"), {}))
    monkeypatch.setattr(sp, "_check_novnc_backend_vnc", lambda: _record("PASS"))
    monkeypatch.setattr(sp, "_check_state_pack_integrity", lambda _root: _record("PASS"))
    monkeypatch.setattr(sp, "_check_state_pack_freshness", lambda _root: _record("PASS"))
    monkeypatch.setattr(sp, "_check_systemd_failed_units", lambda: _record("PASS"))
    monkeypatch.setattr(
        sp,
        "_latest_canary_result",
        lambda _root: (
            {
                "status": "PASS",
                "core_status": "PASS",
                "optional_status": "PASS",
                "core_failed_checks": [],
                "optional_failed_checks": [],
            },
            artifacts_root / "system" / "canary" / "canary_mock" / "result.json",
        ),
    )
    monkeypatch.setattr(sp, "_detect_active_status", lambda _hq: _base_active())


def test_preflight_schema_and_latest_pointer(monkeypatch, tmp_path: Path) -> None:
    artifacts_root = tmp_path / "artifacts"
    _patch_default_checks(monkeypatch, artifacts_root=artifacts_root)

    payload = sp.run_soma_preflight(
        artifacts_root=artifacts_root,
        hq_base="http://127.0.0.1:8787",
        run_id="soma_preflight_test_schema",
        mock=False,
    )

    assert payload["status"] == "GO"
    assert payload["reasons"] == []
    assert payload["run_id"] == "soma_preflight_test_schema"
    assert isinstance(payload["checks"], dict)

    required_checks = {
        "host_executor_reachable",
        "frontdoor_ws_upgrade_websockify",
        "novnc_ws_endpoint",
        "novnc_backend_vnc_5900",
        "state_pack_integrity",
        "state_pack_freshness",
        "systemd_failed_units",
        "canary_core",
        "canary_optional",
    }
    assert required_checks.issubset(payload["checks"].keys())
    for key in required_checks:
        assert payload["checks"][key]["status"] in {"PASS", "FAIL", "WARN"}

    result_path = artifacts_root / "system" / "soma_preflight" / "soma_preflight_test_schema" / "soma_preflight.json"
    latest_path = artifacts_root / "system" / "soma_preflight" / "LATEST.json"
    assert result_path.is_file()
    assert latest_path.is_file()

    latest = json.loads(latest_path.read_text(encoding="utf-8"))
    assert latest["run_id"] == "soma_preflight_test_schema"
    assert latest["status"] == "GO"
    assert latest["result_path"] == "artifacts/system/soma_preflight/soma_preflight_test_schema/soma_preflight.json"


def test_classification_go(monkeypatch, tmp_path: Path) -> None:
    artifacts_root = tmp_path / "artifacts"
    _patch_default_checks(monkeypatch, artifacts_root=artifacts_root)
    payload = sp.evaluate_preflight(artifacts_root=artifacts_root, hq_base="http://127.0.0.1:8787")
    assert payload["status"] == "GO"
    assert payload["reasons"] == []
    assert payload["active_status"] == "idle"


def test_classification_human_only_waiting(monkeypatch, tmp_path: Path) -> None:
    artifacts_root = tmp_path / "artifacts"
    _patch_default_checks(monkeypatch, artifacts_root=artifacts_root)
    active = _base_active()
    active["active_status"] = "waiting"
    active["active_run_id"] = "run_waiting_1"
    active["novnc_url"] = "https://aiops-1.tailc75c62.ts.net/novnc/vnc.html?path=/websockify"
    active["gate_expiry"] = "2026-03-06T12:00:00Z"
    monkeypatch.setattr(sp, "_detect_active_status", lambda _hq: active)

    payload = sp.evaluate_preflight(artifacts_root=artifacts_root, hq_base="http://127.0.0.1:8787")
    assert payload["status"] == "HUMAN_ONLY"
    assert payload["reasons"] == ["WAITING_FOR_HUMAN"]
    assert payload["novnc_url"] == active["novnc_url"]
    assert payload["gate_expiry"] == active["gate_expiry"]


def test_classification_waiting_missing_metadata_is_no_go(monkeypatch, tmp_path: Path) -> None:
    artifacts_root = tmp_path / "artifacts"
    _patch_default_checks(monkeypatch, artifacts_root=artifacts_root)
    active = _base_active()
    active["active_status"] = "waiting"
    active["active_run_id"] = "run_waiting_2"
    active["novnc_url"] = None
    active["gate_expiry"] = ""
    monkeypatch.setattr(sp, "_detect_active_status", lambda _hq: active)

    payload = sp.evaluate_preflight(artifacts_root=artifacts_root, hq_base="http://127.0.0.1:8787")
    assert payload["status"] == "NO_GO"
    assert payload["reasons"] == ["WAITING_FOR_HUMAN_METADATA_MISSING"]


def test_classification_running_is_no_go(monkeypatch, tmp_path: Path) -> None:
    artifacts_root = tmp_path / "artifacts"
    _patch_default_checks(monkeypatch, artifacts_root=artifacts_root)
    active = _base_active()
    active["active_status"] = "running"
    active["active_run_id"] = "run_active_1"
    monkeypatch.setattr(sp, "_detect_active_status", lambda _hq: active)

    payload = sp.evaluate_preflight(artifacts_root=artifacts_root, hq_base="http://127.0.0.1:8787")
    assert payload["status"] == "NO_GO"
    assert payload["reasons"] == ["ALREADY_RUNNING"]


def test_classification_host_executor_failure_is_no_go(monkeypatch, tmp_path: Path) -> None:
    artifacts_root = tmp_path / "artifacts"
    _patch_default_checks(monkeypatch, artifacts_root=artifacts_root)
    monkeypatch.setattr(sp, "_check_host_executor_reachable", lambda _hq: _record("FAIL"))

    payload = sp.evaluate_preflight(artifacts_root=artifacts_root, hq_base="http://127.0.0.1:8787")
    assert payload["status"] == "NO_GO"
    assert payload["reasons"] == ["HOST_EXECUTOR_UNREACHABLE"]


def test_classification_canary_core_failure_is_no_go(monkeypatch, tmp_path: Path) -> None:
    artifacts_root = tmp_path / "artifacts"
    _patch_default_checks(monkeypatch, artifacts_root=artifacts_root)
    monkeypatch.setattr(
        sp,
        "_latest_canary_result",
        lambda _root: (
            {
                "status": "DEGRADED",
                "core_status": "FAIL",
                "optional_status": "PASS",
                "core_failed_checks": ["novnc_audit_failed"],
                "optional_failed_checks": [],
            },
            artifacts_root / "system" / "canary" / "canary_mock" / "result.json",
        ),
    )

    payload = sp.evaluate_preflight(artifacts_root=artifacts_root, hq_base="http://127.0.0.1:8787")
    assert payload["status"] == "NO_GO"
    assert payload["reasons"] == ["CANARY_CORE_DEGRADED"]
    assert payload["checks"]["canary_optional"]["status"] == "PASS"
