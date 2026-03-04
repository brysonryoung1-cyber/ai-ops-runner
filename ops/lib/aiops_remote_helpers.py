"""Helpers for local remote-ops scripts.

These utilities are intentionally stdlib-only so shell wrappers can call them
for deterministic parsing and proof writing.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse


RUNNING_RUN_STATUSES = {"queued", "running"}
TERMINAL_SUCCESS = "SUCCESS"
TERMINAL_WAITING = "WAITING_FOR_HUMAN"
TERMINAL_FAIL = "FAIL"
TERMINAL_RUNNING = "RUNNING"


def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO format."""
    return datetime.now(timezone.utc).isoformat()


def _safe_json_loads(raw: str) -> dict[str, Any] | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def assess_health_public(http_code: int, body_text: str) -> dict[str, Any]:
    """Classify /api/ui/health_public response."""
    payload = _safe_json_loads(body_text)
    ok = bool(payload and payload.get("ok") is True)
    transient_502 = http_code == 502
    if ok:
        state = "OK"
        error_class = None
    elif transient_502:
        state = "TRANSIENT_502"
        error_class = "HTTP_502"
    else:
        state = "ERROR"
        error_class = f"HTTP_{http_code}" if http_code else "HTTP_ERROR"
    return {
        "state": state,
        "ok": ok,
        "transient_502": transient_502,
        "http_code": int(http_code),
        "error_class": error_class,
        "body_json": payload,
        "body_text": (body_text or "").strip(),
    }


def parse_exec_trigger_response(http_code: int, body_text: str) -> dict[str, Any]:
    """Parse POST /api/exec response into ACCEPTED/ALREADY_RUNNING/FAILED."""
    payload = _safe_json_loads(body_text) or {}
    run_id = payload.get("run_id")
    active_run_id = payload.get("active_run_id")
    if http_code in (200, 202) and isinstance(run_id, str) and run_id:
        return {"state": "ACCEPTED", "run_id": run_id, "http_code": http_code, "payload": payload}
    if http_code == 409:
        rid = active_run_id if isinstance(active_run_id, str) and active_run_id else None
        return {"state": "ALREADY_RUNNING", "run_id": rid, "http_code": http_code, "payload": payload}
    message = payload.get("error_class") or payload.get("error") or (body_text or "").strip()[:240]
    return {"state": "FAILED", "run_id": None, "http_code": http_code, "message": message, "payload": payload}


def parse_run_poll_response(body_text: str) -> dict[str, Any]:
    """Parse GET /api/runs?id=<run_id> response."""
    payload = _safe_json_loads(body_text) or {}
    run_obj = payload.get("run")
    if not isinstance(run_obj, dict):
        run_obj = {}
    status = str(run_obj.get("status") or "").strip().lower()
    artifact_dir = run_obj.get("artifact_dir")
    if artifact_dir is not None and not isinstance(artifact_dir, str):
        artifact_dir = str(artifact_dir)
    return {
        "ok": bool(payload.get("ok") is True),
        "status": status,
        "artifact_dir": artifact_dir or None,
        "run": run_obj,
        "payload": payload,
    }


def parse_project_status_response(body_text: str) -> dict[str, Any]:
    """Parse /api/projects/<project>/status for the active run_id."""
    payload = _safe_json_loads(body_text) or {}
    active = payload.get("active_run_id")
    if isinstance(active, str) and active.strip():
        return {"active_run_id": active.strip(), "payload": payload}
    rid = payload.get("run_id")
    if isinstance(rid, str) and rid.strip():
        return {"active_run_id": rid.strip(), "payload": payload}
    run_obj = payload.get("run")
    if isinstance(run_obj, dict):
        for key in ("run_id", "id"):
            v = run_obj.get(key)
            if isinstance(v, str) and v.strip():
                return {"active_run_id": v.strip(), "payload": payload}
    return {"active_run_id": None, "payload": payload}


def parse_artifact_browse_proof(body_text: str) -> dict[str, Any] | None:
    """Parse /api/artifacts/browse response and return PROOF payload (if JSON)."""
    payload = _safe_json_loads(body_text)
    if not payload:
        return None
    content = payload.get("content")
    if not isinstance(content, str):
        return None
    return _safe_json_loads(content)


def classify_soma_terminal_status(
    run_status: str | None,
    proof_payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize Soma terminal state to SUCCESS/WAITING_FOR_HUMAN/FAIL/RUNNING."""
    run_status = (run_status or "").strip().lower()
    proof_status = ""
    novnc_url = ""
    if proof_payload:
        status_val = proof_payload.get("status")
        if status_val is not None:
            proof_status = str(status_val).strip().upper()
        novnc_val = proof_payload.get("novnc_url")
        if isinstance(novnc_val, str):
            novnc_url = novnc_val.strip()
    if proof_status == TERMINAL_WAITING:
        terminal = TERMINAL_WAITING
    elif proof_status == TERMINAL_SUCCESS:
        terminal = TERMINAL_SUCCESS
    elif proof_status in {"FAILURE", "TIMEOUT", TERMINAL_FAIL}:
        terminal = TERMINAL_FAIL
    elif run_status in RUNNING_RUN_STATUSES:
        terminal = TERMINAL_RUNNING
    elif run_status == "success":
        terminal = TERMINAL_SUCCESS
    elif run_status in {"failure", "error"}:
        terminal = TERMINAL_FAIL
    else:
        terminal = TERMINAL_RUNNING
    return {
        "terminal_status": terminal,
        "run_status": run_status,
        "proof_status": proof_status or None,
        "novnc_url": novnc_url or None,
    }


def canonical_novnc_url(base_url: str) -> str:
    """Build canonical noVNC URL from base URL host."""
    parsed = urlparse(base_url)
    host = parsed.netloc or parsed.path
    host = host.strip("/")
    if not host:
        host = "aiops-1.tailc75c62.ts.net"
    return (
        f"https://{host}/novnc/vnc.html?"
        "autoconnect=1&reconnect=true&reconnect_delay=2000&path=/websockify"
    )


def parse_browse_dir_entries(body_text: str) -> list[str]:
    """Extract directory entry names from a browse API response."""
    payload = _safe_json_loads(body_text)
    if not payload:
        return []
    entries = payload.get("entries")
    if not isinstance(entries, list):
        return []
    return [
        e["name"]
        for e in entries
        if isinstance(e, dict) and isinstance(e.get("name"), str) and e.get("type") == "dir"
    ]


def resolve_run_to_done_dir(
    run_id: str,
    dir_entries: list[str],
) -> dict[str, Any]:
    """Resolve run_to_done artifact dir from browse listing and console run_id.

    run_id: Console-generated ID (YYYYMMDDHHMMSS-XXXX).
    dir_entries: Directory names under soma_kajabi/run_to_done/.

    Returns {resolved_dir: str|None, error: str|None}.
    """
    if not run_id or len(run_id) < 14:
        return {"resolved_dir": None, "error": f"invalid run_id: {run_id!r}"}

    ts_raw = run_id[:14]
    date_part = ts_raw[:8]
    time_part = ts_raw[8:14]
    utc_prefix = f"run_to_done_{date_part}T{time_part}Z_"

    matches = [e for e in dir_entries if e.startswith(utc_prefix)]
    if matches:
        matches.sort()
        return {
            "resolved_dir": f"artifacts/soma_kajabi/run_to_done/{matches[-1]}",
            "error": None,
        }

    try:
        run_dt = datetime(
            int(date_part[:4]), int(date_part[4:6]), int(date_part[6:8]),
            int(time_part[:2]), int(time_part[2:4]), int(time_part[4:6]),
            tzinfo=timezone.utc,
        )
    except (ValueError, IndexError):
        return {"resolved_dir": None, "error": f"cannot parse timestamp: {run_id!r}"}

    best_entry: str | None = None
    best_delta = float("inf")
    for entry in dir_entries:
        if not entry.startswith("run_to_done_"):
            continue
        rest = entry[len("run_to_done_"):]
        if len(rest) < 16 or rest[8] != "T" or rest[15] not in ("Z", "z"):
            continue
        try:
            entry_dt = datetime(
                int(rest[:4]), int(rest[4:6]), int(rest[6:8]),
                int(rest[9:11]), int(rest[11:13]), int(rest[13:15]),
                tzinfo=timezone.utc,
            )
        except (ValueError, IndexError):
            continue
        delta = abs((entry_dt - run_dt).total_seconds())
        if delta < best_delta:
            best_delta = delta
            best_entry = entry

    if best_entry is not None and best_delta <= 600:
        return {
            "resolved_dir": f"artifacts/soma_kajabi/run_to_done/{best_entry}",
            "error": None,
        }

    return {
        "resolved_dir": None,
        "error": (
            f"no run_to_done dir within 600s of {run_id}; "
            f"checked {len(dir_entries)} entries"
        ),
    }


def extract_last_json_object(raw_text: str) -> dict[str, Any] | None:
    """Return the last parseable JSON object found in a line-oriented output."""
    for line in reversed((raw_text or "").splitlines()):
        line = line.strip()
        if not line.startswith("{") or not line.endswith("}"):
            continue
        parsed = _safe_json_loads(line)
        if parsed is not None:
            return parsed
    return None


def write_json_file(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(payload), indent=2) + "\n", encoding="utf-8")


def build_apply_result(
    *,
    run_id: str,
    started_at: str,
    finished_at: str,
    host: str,
    base_url: str,
    repo_dir: str,
    health_before: Mapping[str, Any],
    health_after: Mapping[str, Any],
    deploy_ok: bool,
    remediation_attempted: bool,
) -> dict[str, Any]:
    """Build machine-readable RESULT payload for apply_and_prove."""
    before_state = str(health_before.get("state") or "UNKNOWN")
    after_state = str(health_after.get("state") or "UNKNOWN")
    pass_result = bool(deploy_ok) and after_state == "OK"
    status = "PASS" if pass_result else "FAIL"
    summary = (
        f"deploy_ok={str(bool(deploy_ok)).lower()}, "
        f"before={before_state}, after={after_state}, "
        f"remediation_attempted={str(bool(remediation_attempted)).lower()}"
    )
    return {
        "run_id": run_id,
        "status": status,
        "started_at": started_at,
        "finished_at": finished_at,
        "host": host,
        "base_url": base_url,
        "repo_dir": repo_dir,
        "deploy_ok": bool(deploy_ok),
        "remediation_attempted": bool(remediation_attempted),
        "health_before_state": before_state,
        "health_after_state": after_state,
        "summary": summary,
    }
