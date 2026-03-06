#!/usr/bin/env python3
"""Soma GO/NO-GO preflight.

Produces a single machine-readable artifact used by project_autopilot to decide
whether Soma can run now.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ops.lib.aiops_remote_helpers import canonical_novnc_url
from ops.lib.artifacts_root import get_artifacts_root
from ops.lib.exec_trigger import hq_request
from ops.lib.human_gate import read_gate
from ops.lib.state_pack_contract import evaluate_state_pack_freshness, evaluate_state_pack_integrity

SCHEMA_VERSION = 1
DEFAULT_HQ_BASE = "http://127.0.0.1:8787"
DEFAULT_WS_HOLD_SEC = 5
OPTIONAL_CANARY_IDS = {
    "ask_unreachable",
    "ask_smoke_failed",
    "ask_failed",
    "llm_unreachable",
    "llm_degraded",
}
TRACKED_UNIT_GROUPS: dict[str, tuple[str, ...]] = {
    "openclaw-novnc.service": ("openclaw-novnc.service",),
    "openclaw-soma-autopilot.service": ("openclaw-soma-autopilot.service",),
    "ai-ops-runner.service": ("ai-ops-runner.service",),
    "caddy": (
        "caddy.service",
        "caddy",
        "openclaw-frontdoor.service",
        "openclaw-frontdoor",
    ),
    "host_executor": (
        "openclaw-hostd.service",
        "openclaw-hostd",
        "hostd.service",
        "hostd",
    ),
}


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_run_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"soma_preflight_{ts}_{secrets.token_hex(4)}"


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=str(path.parent), delete=False) as tmp:
        tmp.write(content)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    _atomic_write_text(path, json.dumps(payload, indent=2) + "\n")


def _parse_json_object(raw: str) -> dict[str, Any] | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _rel_artifact_path(path: Path, artifacts_root: Path) -> str:
    try:
        rel = path.resolve().relative_to(artifacts_root.resolve())
        return f"artifacts/{rel.as_posix()}"
    except ValueError:
        return str(path)


@dataclass
class CheckRecord:
    status: str
    details: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "details": self.details,
        }


def _hq_get_json(path: str, hq_base: str) -> tuple[int, dict[str, Any] | None, str]:
    code, body = hq_request("GET", path, timeout=20, base_url=hq_base)
    payload = _parse_json_object(body)
    return int(code), payload, body


def _run_script_json(command: list[str], *, timeout_sec: int = 30) -> tuple[int, dict[str, Any] | None, str, str]:
    proc = subprocess.run(
        command,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=timeout_sec,
        check=False,
    )
    payload = _parse_json_object(proc.stdout)
    return proc.returncode, payload, proc.stdout or "", proc.stderr or ""


def _check_host_executor_reachable(hq_base: str) -> CheckRecord:
    code, payload, body = _hq_get_json("/api/host-executor/status", hq_base)
    ok = code == 200 and isinstance(payload, dict) and bool(payload.get("ok"))
    status = "PASS" if ok else "FAIL"
    details: dict[str, Any] = {
        "http_code": code,
        "ok": bool(payload.get("ok")) if isinstance(payload, dict) else False,
    }
    if isinstance(payload, dict):
        details["hostd_status"] = payload.get("hostd_status")
        details["error_class"] = payload.get("error_class")
        details["message_redacted"] = payload.get("message_redacted")
    elif body:
        details["body_sample"] = body[:240]
    return CheckRecord(status=status, details=details)


def _check_health_public(hq_base: str) -> CheckRecord:
    code, payload, body = _hq_get_json("/api/ui/health_public", hq_base)
    ok = code == 200 and isinstance(payload, dict) and bool(payload.get("ok"))
    status = "PASS" if ok else "FAIL"
    details: dict[str, Any] = {
        "http_code": code,
    }
    if isinstance(payload, dict):
        details["ok"] = bool(payload.get("ok"))
        details["build_sha"] = payload.get("build_sha")
        details["server_time"] = payload.get("server_time")
    elif body:
        details["body_sample"] = body[:240]
    return CheckRecord(status=status, details=details)


def _check_ws_paths() -> tuple[CheckRecord, CheckRecord, dict[str, Any] | None]:
    script = REPO_ROOT / "ops" / "scripts" / "novnc_ws_probe.py"
    if not script.exists():
        missing = {
            "error": "ws_probe_script_missing",
            "script": str(script),
        }
        return (
            CheckRecord(status="FAIL", details=missing),
            CheckRecord(status="FAIL", details=missing),
            None,
        )

    host = (
        os.environ.get("OPENCLAW_TS_HOSTNAME", "").strip()
        or os.environ.get("OPENCLAW_TAILSCALE_HOSTNAME", "").strip()
        or "aiops-1.tailc75c62.ts.net"
    )
    hold = int(os.environ.get("OPENCLAW_WS_PROBE_HOLD_SEC", str(DEFAULT_WS_HOLD_SEC)) or DEFAULT_WS_HOLD_SEC)
    rc, payload, stdout, stderr = _run_script_json(
        [
            sys.executable,
            str(script),
            "--host",
            host,
            "--hold",
            str(hold),
            "--all",
        ],
        timeout_sec=max(hold + 20, 30),
    )
    payload = payload or {}
    endpoints = payload.get("endpoints") if isinstance(payload.get("endpoints"), dict) else {}

    ws_websockify = endpoints.get("/websockify") if isinstance(endpoints, dict) else {}
    ws_novnc = endpoints.get("/novnc/websockify") if isinstance(endpoints, dict) else {}

    frontdoor_ok = isinstance(ws_websockify, dict) and bool(ws_websockify.get("ok"))
    novnc_ok = isinstance(ws_novnc, dict) and bool(ws_novnc.get("ok"))

    common = {
        "host": host,
        "hold_sec": hold,
        "subprocess_exit_code": rc,
    }
    if stderr.strip():
        common["stderr"] = stderr.strip()[:300]
    if stdout.strip() and not payload:
        common["stdout_sample"] = stdout.strip()[:300]

    frontdoor_details = {
        **common,
        "endpoint": "/websockify",
        "endpoint_result": ws_websockify if isinstance(ws_websockify, dict) else {},
    }
    novnc_details = {
        **common,
        "endpoint": "/novnc/websockify",
        "endpoint_result": ws_novnc if isinstance(ws_novnc, dict) else {},
    }

    return (
        CheckRecord(status="PASS" if frontdoor_ok else "FAIL", details=frontdoor_details),
        CheckRecord(status="PASS" if novnc_ok else "FAIL", details=novnc_details),
        payload if isinstance(payload, dict) else None,
    )


def _check_novnc_backend_vnc() -> CheckRecord:
    script = REPO_ROOT / "ops" / "scripts" / "novnc_backend_vnc_probe.py"
    if not script.exists():
        return CheckRecord(
            status="FAIL",
            details={"error": "novnc_backend_probe_missing", "script": str(script)},
        )
    rc, payload, stdout, stderr = _run_script_json(
        [sys.executable, str(script), "--host", "127.0.0.1", "--port", "5900", "--timeout-sec", "1.0"],
        timeout_sec=20,
    )
    payload = payload or {}
    ok = bool(payload.get("ok")) and rc == 0
    details: dict[str, Any] = {
        "subprocess_exit_code": rc,
        "host": payload.get("host", "127.0.0.1"),
        "port": payload.get("port", 5900),
        "ok": bool(payload.get("ok")),
        "error": payload.get("error"),
    }
    if stderr.strip():
        details["stderr"] = stderr.strip()[:300]
    if stdout.strip() and not payload:
        details["stdout_sample"] = stdout.strip()[:300]
    return CheckRecord(status="PASS" if ok else "FAIL", details=details)


def _check_state_pack_integrity(artifacts_root: Path) -> CheckRecord:
    payload = evaluate_state_pack_integrity(artifacts_root)
    status = "PASS" if str(payload.get("status")) == "PASS" else "FAIL"
    details = {
        "reason": payload.get("reason"),
        "latest_path": payload.get("latest_path"),
        "result_path": payload.get("result_path"),
        "run_id": payload.get("run_id"),
    }
    return CheckRecord(status=status, details=details)


def _check_state_pack_freshness(artifacts_root: Path) -> CheckRecord:
    payload = evaluate_state_pack_freshness(artifacts_root)
    status = "PASS" if str(payload.get("status")) == "PASS" else "FAIL"
    details = {
        "reason": payload.get("reason"),
        "latest_path": payload.get("latest_path"),
        "run_id": payload.get("run_id"),
        "age_sec": payload.get("age_sec"),
        "threshold_sec": payload.get("threshold_sec"),
    }
    return CheckRecord(status=status, details=details)


def _probe_unit_state(unit: str) -> tuple[str, str, int]:
    proc = subprocess.run(
        ["systemctl", "is-failed", unit],
        capture_output=True,
        text=True,
        timeout=8,
        check=False,
    )
    text = (proc.stdout or proc.stderr or "").strip().lower()
    return unit, text or "unknown", int(proc.returncode)


def _check_systemd_failed_units() -> CheckRecord:
    if not shutil_which("systemctl"):
        return CheckRecord(
            status="WARN",
            details={"error": "systemctl_not_found", "tracked_units": list(TRACKED_UNIT_GROUPS.keys())},
        )

    resolved: dict[str, dict[str, Any]] = {}
    failed: list[str] = []

    for label, candidates in TRACKED_UNIT_GROUPS.items():
        chosen: dict[str, Any] | None = None
        for cand in candidates:
            unit, state, rc = _probe_unit_state(cand)
            cand_result = {"unit": unit, "state": state, "rc": rc}
            if chosen is None:
                chosen = cand_result
            if state in {"active", "inactive", "failed", "activating", "deactivating", "reloading", "maintenance"}:
                chosen = cand_result
                if state != "not-found":
                    break
        if chosen is None:
            chosen = {"unit": candidates[0], "state": "unknown", "rc": -1}
        resolved[label] = chosen
        if chosen.get("state") == "failed":
            failed.append(str(chosen.get("unit") or label))

    status = "FAIL" if failed else "PASS"
    return CheckRecord(
        status=status,
        details={
            "failed_units": failed,
            "tracked_units": resolved,
        },
    )


def shutil_which(cmd: str) -> str | None:
    for part in os.environ.get("PATH", "").split(":"):
        if not part:
            continue
        candidate = Path(part) / cmd
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _latest_canary_result(artifacts_root: Path) -> tuple[dict[str, Any] | None, Path | None]:
    canary_root = artifacts_root / "system" / "canary"
    if not canary_root.is_dir():
        return None, None
    try:
        dirs = sorted([p for p in canary_root.iterdir() if p.is_dir()], key=lambda p: p.name, reverse=True)
    except OSError:
        return None, None
    for entry in dirs:
        result_path = entry / "result.json"
        payload = _read_json(result_path)
        if isinstance(payload, dict):
            return payload, result_path
    return None, None


def _derive_canary_contract(canary_payload: dict[str, Any] | None) -> tuple[CheckRecord, CheckRecord]:
    if not isinstance(canary_payload, dict):
        missing = {
            "error": "canary_result_missing",
        }
        return (
            CheckRecord(status="FAIL", details=missing),
            CheckRecord(status="WARN", details={"error": "canary_result_missing"}),
        )

    checks_obj = canary_payload.get("checks") if isinstance(canary_payload.get("checks"), dict) else {}
    core_failed = [str(x) for x in (canary_payload.get("core_failed_checks") or []) if str(x).strip()]
    optional_failed = [str(x) for x in (canary_payload.get("optional_failed_checks") or []) if str(x).strip()]

    core_status = str(canary_payload.get("core_status") or "").upper()
    optional_status = str(canary_payload.get("optional_status") or "").upper()

    if not core_status:
        status = str(canary_payload.get("status") or "").upper()
        failed_invariant = str(canary_payload.get("failed_invariant") or "").strip()
        if failed_invariant:
            if failed_invariant in OPTIONAL_CANARY_IDS:
                optional_failed = [failed_invariant]
                core_failed = []
            else:
                core_failed = [failed_invariant]
        if status == "PASS":
            core_status = "PASS"
            optional_status = "PASS"
        elif core_failed:
            core_status = "FAIL"
            optional_status = "PASS" if not optional_failed else "WARN"
        elif optional_failed:
            core_status = "PASS"
            optional_status = "WARN"
        else:
            core_status = "FAIL"
            optional_status = "WARN"

    core_record = CheckRecord(
        status="PASS" if core_status == "PASS" else "FAIL",
        details={
            "core_status": core_status,
            "failed_checks": core_failed,
            "checks": checks_obj,
        },
    )
    optional_record = CheckRecord(
        status="PASS" if optional_status == "PASS" else "WARN",
        details={
            "optional_status": optional_status,
            "failed_checks": optional_failed,
            "checks": checks_obj,
        },
    )
    return core_record, optional_record


def _load_waiting_metadata_from_status(status_payload: dict[str, Any] | None) -> tuple[str | None, str | None, str | None]:
    if not isinstance(status_payload, dict):
        return None, None, None
    run_id = str(
        status_payload.get("active_run_id")
        or status_payload.get("human_gate_run_id")
        or status_payload.get("last_run_id")
        or ""
    ).strip() or None
    novnc_url_raw = status_payload.get("human_gate_novnc_url") or status_payload.get("novnc_url")
    novnc_url = str(novnc_url_raw).strip() if isinstance(novnc_url_raw, str) and novnc_url_raw.strip() else None
    if novnc_url:
        novnc_url = canonical_novnc_url(novnc_url)
    expiry_raw = status_payload.get("human_gate_expires_at")
    expiry = str(expiry_raw).strip() if isinstance(expiry_raw, str) and expiry_raw.strip() else None
    return run_id, novnc_url, expiry


def _detect_active_status(hq_base: str) -> dict[str, Any]:
    status_code, status_payload, _status_body = _hq_get_json("/api/projects/soma_kajabi/status", hq_base)
    lock_rt_code, lock_rt_payload, _ = _hq_get_json("/api/exec?check=lock&action=soma_run_to_done", hq_base)
    lock_af_code, lock_af_payload, _ = _hq_get_json("/api/exec?check=lock&action=soma_kajabi_auto_finish", hq_base)

    active_status = "idle"
    active_run_id: str | None = None
    novnc_url: str | None = None
    gate_expiry: str | None = None

    current_status = str(status_payload.get("current_status") or "") if isinstance(status_payload, dict) else ""
    if current_status == "WAITING_FOR_HUMAN":
        active_status = "waiting"
        active_run_id, novnc_url, gate_expiry = _load_waiting_metadata_from_status(status_payload)

    locked_run = bool(lock_rt_payload.get("locked")) if isinstance(lock_rt_payload, dict) else False
    locked_auto = bool(lock_af_payload.get("locked")) if isinstance(lock_af_payload, dict) else False

    if active_status == "idle" and (locked_run or locked_auto):
        active_status = "running"
        rid = None
        if isinstance(lock_af_payload, dict):
            rid = str(lock_af_payload.get("active_run_id") or "").strip() or None
        if not rid and isinstance(lock_rt_payload, dict):
            rid = str(lock_rt_payload.get("active_run_id") or "").strip() or None
        active_run_id = rid

        if rid:
            run_code, run_payload, _ = _hq_get_json(f"/api/runs?id={rid}", hq_base)
            run_obj = run_payload.get("run") if isinstance(run_payload, dict) else None
            run_state = str(run_obj.get("status") or "") if isinstance(run_obj, dict) else ""
            if run_code == 404:
                active_status = "orphaned"
            elif run_state and run_state not in {"running", "queued"}:
                active_status = "orphaned"

    if active_status == "idle":
        gate = read_gate("soma_kajabi")
        if gate.get("active") is True and isinstance(gate.get("gate"), dict):
            active_status = "waiting"
            gate_payload = gate["gate"]
            active_run_id = str(gate_payload.get("run_id") or "").strip() or active_run_id
            gate_novnc = str(gate_payload.get("novnc_url") or "").strip()
            if gate_novnc:
                novnc_url = canonical_novnc_url(gate_novnc)
            gate_expiry = str(gate_payload.get("expires_at") or "").strip() or gate_expiry

    return {
        "status_endpoint_http_code": status_code,
        "status_payload": status_payload if isinstance(status_payload, dict) else {},
        "lock_run_http_code": lock_rt_code,
        "lock_run_payload": lock_rt_payload if isinstance(lock_rt_payload, dict) else {},
        "lock_auto_http_code": lock_af_code,
        "lock_auto_payload": lock_af_payload if isinstance(lock_af_payload, dict) else {},
        "active_status": active_status,
        "active_run_id": active_run_id,
        "novnc_url": novnc_url,
        "gate_expiry": gate_expiry,
    }


def evaluate_preflight(
    *,
    artifacts_root: Path,
    hq_base: str,
    mock: bool = False,
) -> dict[str, Any]:
    checks: dict[str, CheckRecord]
    if mock:
        checks = {
            "host_executor_reachable": CheckRecord(status="PASS", details={"mock": True}),
            "frontdoor_ws_upgrade_websockify": CheckRecord(status="PASS", details={"mock": True}),
            "novnc_ws_endpoint": CheckRecord(status="PASS", details={"mock": True}),
            "novnc_backend_vnc_5900": CheckRecord(status="PASS", details={"mock": True}),
            "state_pack_integrity": CheckRecord(status="PASS", details={"mock": True}),
            "state_pack_freshness": CheckRecord(status="PASS", details={"mock": True}),
            "systemd_failed_units": CheckRecord(status="PASS", details={"mock": True}),
            "canary_core": CheckRecord(status="PASS", details={"mock": True}),
            "canary_optional": CheckRecord(status="PASS", details={"mock": True}),
        }
        active = {
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
        canary_result_rel = None
    else:
        health_public = _check_health_public(hq_base)
        host_executor = _check_host_executor_reachable(hq_base)
        ws_frontdoor, ws_novnc, ws_payload = _check_ws_paths()
        novnc_backend = _check_novnc_backend_vnc()
        state_integrity = _check_state_pack_integrity(artifacts_root)
        state_freshness = _check_state_pack_freshness(artifacts_root)
        systemd_failed = _check_systemd_failed_units()

        canary_payload, canary_result_path = _latest_canary_result(artifacts_root)
        canary_core, canary_optional = _derive_canary_contract(canary_payload)

        checks = {
            "health_public": health_public,
            "host_executor_reachable": host_executor,
            "frontdoor_ws_upgrade_websockify": ws_frontdoor,
            "novnc_ws_endpoint": ws_novnc,
            "novnc_backend_vnc_5900": novnc_backend,
            "state_pack_integrity": state_integrity,
            "state_pack_freshness": state_freshness,
            "systemd_failed_units": systemd_failed,
            "canary_core": canary_core,
            "canary_optional": canary_optional,
        }
        active = _detect_active_status(hq_base)
        canary_result_rel = (
            _rel_artifact_path(canary_result_path, artifacts_root) if canary_result_path is not None else None
        )
        if isinstance(ws_payload, dict):
            checks["frontdoor_ws_upgrade_websockify"].details["probe_payload"] = ws_payload
            checks["novnc_ws_endpoint"].details["probe_payload"] = ws_payload

    reasons: list[str] = []
    status = "GO"
    novnc_url = active.get("novnc_url")
    gate_expiry = active.get("gate_expiry")
    active_run_id = active.get("active_run_id")
    active_status = str(active.get("active_status") or "idle")

    if active_status == "waiting":
        if isinstance(novnc_url, str) and novnc_url and isinstance(gate_expiry, str) and gate_expiry:
            status = "HUMAN_ONLY"
            reasons = ["WAITING_FOR_HUMAN"]
        else:
            status = "NO_GO"
            reasons = ["WAITING_FOR_HUMAN_METADATA_MISSING"]
    elif active_status == "running":
        status = "NO_GO"
        reasons = ["ALREADY_RUNNING"]
    elif active_status == "orphaned":
        status = "NO_GO"
        reasons = ["ORPHANED_ACTIVE_RUN"]
    else:
        reason_by_check = {
            "host_executor_reachable": "HOST_EXECUTOR_UNREACHABLE",
            "frontdoor_ws_upgrade_websockify": "FRONTDOOR_WS_UPGRADE_FAILED",
            "novnc_ws_endpoint": "NOVNC_WS_ENDPOINT_FAILED",
            "novnc_backend_vnc_5900": "NOVNC_BACKEND_VNC_5900_FAILED",
            "state_pack_integrity": "STATE_PACK_INTEGRITY_FAIL",
            "state_pack_freshness": "STATE_PACK_FRESHNESS_FAIL",
            "systemd_failed_units": "TRACKED_SYSTEMD_UNITS_FAILED",
            "canary_core": "CANARY_CORE_DEGRADED",
        }
        failed_reasons = [
            reason
            for check_name, reason in reason_by_check.items()
            if checks[check_name].status == "FAIL"
        ]
        if failed_reasons:
            status = "NO_GO"
            reasons = failed_reasons

    output = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now_utc_iso(),
        "status": status,
        "reasons": reasons,
        "novnc_url": novnc_url if status == "HUMAN_ONLY" else None,
        "gate_expiry": gate_expiry if status == "HUMAN_ONLY" else None,
        "active_run_id": active_run_id,
        "active_status": active_status,
        "checks": {
            name: rec.to_dict()
            for name, rec in checks.items()
        },
        "active_details": {
            "status_endpoint_http_code": active.get("status_endpoint_http_code"),
            "lock_run_http_code": active.get("lock_run_http_code"),
            "lock_auto_http_code": active.get("lock_auto_http_code"),
            "status_payload": active.get("status_payload"),
            "lock_run_payload": active.get("lock_run_payload"),
            "lock_auto_payload": active.get("lock_auto_payload"),
        },
    }
    if canary_result_rel:
        output["checks"]["canary_core"]["details"]["result_path"] = canary_result_rel
        output["checks"]["canary_optional"]["details"]["result_path"] = canary_result_rel

    return output


def run_soma_preflight(
    *,
    artifacts_root: Path,
    hq_base: str,
    run_id: str | None = None,
    mock: bool = False,
) -> dict[str, Any]:
    resolved_run_id = (run_id or "").strip() or _build_run_id()
    bundle_dir = artifacts_root / "system" / "soma_preflight" / resolved_run_id
    bundle_dir.mkdir(parents=True, exist_ok=True)

    payload = evaluate_preflight(artifacts_root=artifacts_root, hq_base=hq_base, mock=mock)
    payload["run_id"] = resolved_run_id
    payload["bundle_dir"] = _rel_artifact_path(bundle_dir, artifacts_root)

    result_path = bundle_dir / "soma_preflight.json"
    _atomic_write_json(result_path, payload)

    latest_path = artifacts_root / "system" / "soma_preflight" / "LATEST.json"
    latest_payload = {
        "schema_version": SCHEMA_VERSION,
        "run_id": resolved_run_id,
        "status": payload.get("status"),
        "generated_at": payload.get("generated_at"),
        "latest_path": _rel_artifact_path(bundle_dir, artifacts_root),
        "result_path": _rel_artifact_path(result_path, artifacts_root),
    }
    _atomic_write_json(latest_path, latest_payload)

    payload["result_path"] = latest_payload["result_path"]
    return payload


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Soma GO/NO-GO preflight")
    parser.add_argument("--hq-base", default=DEFAULT_HQ_BASE)
    parser.add_argument("--artifacts-root", default="")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--mock", action="store_true", help="Return deterministic GO payload for tests")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(list(argv or []))
    artifacts_root = Path(args.artifacts_root).expanduser() if str(args.artifacts_root).strip() else get_artifacts_root(REPO_ROOT)
    payload = run_soma_preflight(
        artifacts_root=artifacts_root,
        hq_base=str(args.hq_base),
        run_id=str(args.run_id),
        mock=bool(args.mock),
    )
    print(json.dumps(payload, indent=2))
    return 0 if payload.get("status") in {"GO", "HUMAN_ONLY"} else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
