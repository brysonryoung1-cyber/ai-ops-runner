"""Unit tests for ops.lib.aiops_remote_helpers."""

from __future__ import annotations

import json
from pathlib import Path

from ops.lib.aiops_remote_helpers import (
    TERMINAL_FAIL,
    TERMINAL_SUCCESS,
    TERMINAL_WAITING,
    assess_health_public,
    build_apply_result,
    canonical_novnc_url,
    classify_soma_terminal_status,
    extract_last_json_object,
    parse_browse_dir_entries,
    parse_exec_trigger_response,
    parse_project_status_response,
    parse_run_poll_response,
    resolve_run_to_done_dir,
    write_json_file,
)


def test_assess_health_public_ok() -> None:
    payload = {"ok": True, "build_sha": "abc123"}
    parsed = assess_health_public(200, json.dumps(payload))
    assert parsed["state"] == "OK"
    assert parsed["ok"] is True
    assert parsed["transient_502"] is False
    assert parsed["body_json"]["build_sha"] == "abc123"


def test_assess_health_public_transient_502() -> None:
    parsed = assess_health_public(502, "Bad Gateway")
    assert parsed["state"] == "TRANSIENT_502"
    assert parsed["ok"] is False
    assert parsed["transient_502"] is True
    assert parsed["error_class"] == "HTTP_502"


def test_parse_exec_trigger_response_already_running() -> None:
    body = json.dumps({"error_class": "ALREADY_RUNNING", "active_run_id": "run-123"})
    parsed = parse_exec_trigger_response(409, body)
    assert parsed["state"] == "ALREADY_RUNNING"
    assert parsed["run_id"] == "run-123"


def test_parse_exec_trigger_409_without_active_run_id() -> None:
    body = json.dumps({"error_class": "ALREADY_RUNNING"})
    parsed = parse_exec_trigger_response(409, body)
    assert parsed["state"] == "ALREADY_RUNNING"
    assert parsed["run_id"] is None


def test_parse_project_status_explicit_active_run_id() -> None:
    body = json.dumps({"active_run_id": "run-abc", "run_id": "run-old"})
    parsed = parse_project_status_response(body)
    assert parsed["active_run_id"] == "run-abc"


def test_parse_project_status_fallback_to_run_id() -> None:
    body = json.dumps({"run_id": "run-456"})
    parsed = parse_project_status_response(body)
    assert parsed["active_run_id"] == "run-456"


def test_parse_project_status_nested_run_object() -> None:
    body = json.dumps({"run": {"run_id": "run-nested"}})
    parsed = parse_project_status_response(body)
    assert parsed["active_run_id"] == "run-nested"


def test_parse_project_status_empty_body() -> None:
    parsed = parse_project_status_response("")
    assert parsed["active_run_id"] is None


def test_parse_run_poll_response_extracts_status_and_artifact_dir() -> None:
    body = json.dumps(
        {
            "ok": True,
            "run": {
                "run_id": "run-xyz",
                "status": "running",
                "artifact_dir": "artifacts/soma_kajabi/run_to_done/run-xyz",
            },
        }
    )
    parsed = parse_run_poll_response(body)
    assert parsed["ok"] is True
    assert parsed["status"] == "running"
    assert parsed["artifact_dir"] == "artifacts/soma_kajabi/run_to_done/run-xyz"


def test_classify_soma_terminal_status_waiting_from_proof() -> None:
    parsed = classify_soma_terminal_status(
        "success",
        {"status": "WAITING_FOR_HUMAN", "novnc_url": "https://x.ts.net/novnc/vnc.html?path=/websockify"},
    )
    assert parsed["terminal_status"] == TERMINAL_WAITING
    assert parsed["novnc_url"].startswith("https://x.ts.net/")


def test_classify_soma_terminal_status_fail_from_run_status() -> None:
    parsed = classify_soma_terminal_status("failure", {})
    assert parsed["terminal_status"] == TERMINAL_FAIL


def test_extract_last_json_object_reads_final_line() -> None:
    out = "\n".join(
        [
            "random logs",
            '{"ok": true, "status": "SUCCESS"}',
            "tail line",
            '{"ok": false, "status": "WAITING_FOR_HUMAN", "novnc_url": "https://n.ts.net"}',
        ]
    )
    parsed = extract_last_json_object(out)
    assert parsed is not None
    assert parsed["status"] == "WAITING_FOR_HUMAN"
    assert parsed["novnc_url"] == "https://n.ts.net"


def test_build_apply_result_and_write_json(tmp_path: Path) -> None:
    before = {"state": "TRANSIENT_502"}
    after = {"state": "OK"}
    result = build_apply_result(
        run_id="run-1",
        started_at="2026-03-03T00:00:00Z",
        finished_at="2026-03-03T00:01:00Z",
        host="aiops-1",
        base_url="https://aiops-1.tailc75c62.ts.net",
        repo_dir="/opt/ai-ops-runner",
        health_before=before,
        health_after=after,
        deploy_ok=True,
        remediation_attempted=True,
    )
    assert result["status"] == "PASS"
    assert result["health_before_state"] == "TRANSIENT_502"
    assert result["health_after_state"] == "OK"

    out = tmp_path / "RESULT.json"
    write_json_file(out, result)
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["status"] == "PASS"
    assert "remediation_attempted=true" in saved["summary"]


def test_canonical_novnc_url_uses_base_host() -> None:
    url = canonical_novnc_url("https://aiops-1.tailc75c62.ts.net")
    assert url.startswith("https://aiops-1.tailc75c62.ts.net/novnc/vnc.html?")
    assert "path=/websockify" in url


def test_classify_success_without_proof_uses_run_success() -> None:
    parsed = classify_soma_terminal_status("success", None)
    assert parsed["terminal_status"] == TERMINAL_SUCCESS


# --- parse_browse_dir_entries ---

def test_parse_browse_dir_entries_extracts_dirs() -> None:
    body = json.dumps({
        "entries": [
            {"name": "run_to_done_20260303T234225Z_a1b2c3d4", "type": "dir"},
            {"name": "run_to_done_20260304T010000Z_deadbeef", "type": "dir"},
            {"name": "PROOF.json", "type": "file", "size": 512},
        ]
    })
    entries = parse_browse_dir_entries(body)
    assert entries == [
        "run_to_done_20260303T234225Z_a1b2c3d4",
        "run_to_done_20260304T010000Z_deadbeef",
    ]


def test_parse_browse_dir_entries_empty_body() -> None:
    assert parse_browse_dir_entries("") == []
    assert parse_browse_dir_entries("{}") == []
    assert parse_browse_dir_entries('{"entries": []}') == []


# --- resolve_run_to_done_dir ---

def test_resolve_exact_timestamp_match() -> None:
    run_id = "20260303234225-a1b2"
    entries = [
        "run_to_done_20260303T234225Z_deadbeef",
        "run_to_done_20260303T200000Z_old00000",
    ]
    result = resolve_run_to_done_dir(run_id, entries)
    assert result["resolved_dir"] == "artifacts/soma_kajabi/run_to_done/run_to_done_20260303T234225Z_deadbeef"
    assert result["error"] is None


def test_resolve_picks_newest_on_multiple_exact_matches() -> None:
    run_id = "20260303234225-a1b2"
    entries = [
        "run_to_done_20260303T234225Z_aaaa0000",
        "run_to_done_20260303T234225Z_zzzz9999",
    ]
    result = resolve_run_to_done_dir(run_id, entries)
    assert result["resolved_dir"].endswith("run_to_done_20260303T234225Z_zzzz9999")


def test_resolve_fallback_within_600s() -> None:
    run_id = "20260303234225-a1b2"
    entries = [
        "run_to_done_20260303T234300Z_close000",
        "run_to_done_20260303T200000Z_far00000",
    ]
    result = resolve_run_to_done_dir(run_id, entries)
    assert result["resolved_dir"] == "artifacts/soma_kajabi/run_to_done/run_to_done_20260303T234300Z_close000"
    assert result["error"] is None


def test_resolve_rejects_beyond_600s() -> None:
    run_id = "20260303234225-a1b2"
    entries = [
        "run_to_done_20260304T010000Z_toofar00",
    ]
    result = resolve_run_to_done_dir(run_id, entries)
    assert result["resolved_dir"] is None
    assert result["error"] is not None
    assert "no run_to_done dir within 600s" in result["error"]


def test_resolve_invalid_run_id() -> None:
    result = resolve_run_to_done_dir("short", [])
    assert result["resolved_dir"] is None
    assert "invalid run_id" in result["error"]


def test_resolve_empty_entries() -> None:
    result = resolve_run_to_done_dir("20260303234225-a1b2", [])
    assert result["resolved_dir"] is None
    assert "checked 0 entries" in result["error"]
