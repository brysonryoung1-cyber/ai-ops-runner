"""Core platform/transport checks for doctor matrix."""

from __future__ import annotations

import json
from typing import Any

from ops.lib.aiops_remote_helpers import (
    assess_health_public,
    parse_exec_trigger_response,
)

from ..runtime import CheckBuilder, CheckSpec, MatrixRuntime


def _is_browse_ok(payload: Any, http_code: int) -> bool:
    return bool(http_code == 200 and isinstance(payload, dict) and isinstance(payload.get("entries"), list))


def check_hq_health(builder: CheckBuilder, runtime: MatrixRuntime):
    frontdoor = builder.request(
        label="frontdoor_health_public",
        base_label="frontdoor",
        path="/api/ui/health_public",
        timeout=10,
    )
    remote_localhost = builder.request(
        label="remote_localhost_health_public",
        base_label="remote_localhost",
        path="/api/ui/health_public",
        timeout=10,
    )

    frontdoor_assess = assess_health_public(frontdoor.http_code, frontdoor.body_text)
    remote_localhost_assess = assess_health_public(remote_localhost.http_code, remote_localhost.body_text)

    builder.write_json(
        "parsed_health.json",
        {
            "frontdoor": frontdoor_assess,
            "remote_localhost": remote_localhost_assess,
        },
    )

    frontdoor_ok = bool(frontdoor_assess.get("ok") is True)
    remote_localhost_ok = bool(remote_localhost_assess.get("ok") is True)
    remote_localhost_available = remote_localhost.http_code not in (-1, 0)

    details = {
        "frontdoor_http_code": frontdoor.http_code,
        "remote_localhost_http_code": remote_localhost.http_code,
        "frontdoor_ok": frontdoor_ok,
        "remote_localhost_ok": remote_localhost_ok,
        "remote_localhost_available": remote_localhost_available,
        "frontdoor_build_sha": (frontdoor_assess.get("body_json") or {}).get("build_sha") if isinstance(frontdoor_assess.get("body_json"), dict) else None,
        "remote_localhost_build_sha": (remote_localhost_assess.get("body_json") or {}).get("build_sha") if isinstance(remote_localhost_assess.get("body_json"), dict) else None,
    }

    if frontdoor_ok:
        message = "health_public PASS on frontdoor; remote_localhost optional probe recorded"
        return builder.finalize(status="PASS", message=message, details=details)

    if not frontdoor_ok:
        return builder.finalize(
            status="FAIL",
            message=f"frontdoor health_public failed (HTTP {frontdoor.http_code})",
            error_class=str(frontdoor_assess.get("error_class") or "HQ_HEALTH_FRONTDOOR_FAIL"),
            details=details,
        )

    return builder.finalize(
        status="FAIL",
        message=f"remote_localhost health_public failed while reachable (HTTP {remote_localhost.http_code})",
        error_class=str(remote_localhost_assess.get("error_class") or "HQ_HEALTH_REMOTE_LOCALHOST_FAIL"),
        details=details,
    )


def check_hostd_status(builder: CheckBuilder, runtime: MatrixRuntime):
    frontdoor = builder.request(
        label="frontdoor_hostd_status",
        base_label="frontdoor",
        path="/api/host-executor/status",
        timeout=10,
    )
    remote_localhost = builder.request(
        label="remote_localhost_hostd_status",
        base_label="remote_localhost",
        path="/api/host-executor/status",
        timeout=10,
    )

    primary = frontdoor if isinstance(frontdoor.payload, dict) else remote_localhost
    payload = primary.payload if isinstance(primary.payload, dict) else {}

    hostd_ok = bool(payload.get("ok") is True)
    hostd_status = str(payload.get("hostd_status") or "").strip().lower()
    status_ok = hostd_ok and hostd_status == "up"

    details = {
        "frontdoor_http_code": frontdoor.http_code,
        "remote_localhost_http_code": remote_localhost.http_code,
        "selected_source": primary.base_label,
        "selected_http_code": primary.http_code,
        "hostd_ok": hostd_ok,
        "hostd_status": hostd_status,
    }

    if status_ok:
        return builder.finalize(
            status="PASS",
            message=f"host executor status PASS via {primary.base_label}",
            details=details,
        )

    err = None
    if isinstance(payload, dict):
        err = payload.get("error_class") or payload.get("error")
    return builder.finalize(
        status="FAIL",
        message=(
            f"host executor status FAIL via {primary.base_label} "
            f"(HTTP {primary.http_code})"
        ),
        error_class=str(err or "HOSTD_STATUS_FAIL"),
        details=details,
    )


def check_browse_transport(builder: CheckBuilder, runtime: MatrixRuntime):
    path = "/api/artifacts/browse?path="
    frontdoor = builder.request(
        label="frontdoor_browse_root",
        base_label="frontdoor",
        path=path,
        timeout=10,
    )
    remote_localhost = builder.request(
        label="remote_localhost_browse_root",
        base_label="remote_localhost",
        path=path,
        timeout=10,
    )

    frontdoor_ok = _is_browse_ok(frontdoor.payload, frontdoor.http_code)
    remote_localhost_ok = _is_browse_ok(remote_localhost.payload, remote_localhost.http_code)

    details = {
        "path": path,
        "frontdoor_http_code": frontdoor.http_code,
        "remote_localhost_http_code": remote_localhost.http_code,
        "frontdoor_ok": frontdoor_ok,
        "remote_localhost_ok": remote_localhost_ok,
        "frontdoor_entries_count": len(frontdoor.payload.get("entries", [])) if isinstance(frontdoor.payload, dict) and isinstance(frontdoor.payload.get("entries"), list) else None,
        "remote_localhost_entries_count": len(remote_localhost.payload.get("entries", [])) if isinstance(remote_localhost.payload, dict) and isinstance(remote_localhost.payload.get("entries"), list) else None,
    }

    if frontdoor_ok:
        return builder.finalize(
            status="PASS",
            message="browse transport PASS via frontdoor",
            details=details,
        )
    if remote_localhost_ok:
        return builder.finalize(
            status="PASS",
            message="browse transport PASS via remote_localhost fallback",
            details=details,
        )

    return builder.finalize(
        status="FAIL",
        message="browse transport failed on frontdoor and remote_localhost fallback",
        error_class="BROWSE_TRANSPORT_FAIL",
        details=details,
    )


def check_concurrency_semantics(builder: CheckBuilder, runtime: MatrixRuntime):
    synthetic = parse_exec_trigger_response(
        409,
        json.dumps({"error_class": "ALREADY_RUNNING", "active_run_id": "run-abc123"}),
    )
    builder.write_json("synthetic_409_parse.json", synthetic)

    path = "/api/exec?check=lock&action=soma_run_to_done"
    frontdoor = builder.request(
        label="frontdoor_exec_lock",
        base_label="frontdoor",
        path=path,
        timeout=10,
    )
    remote_localhost = builder.request(
        label="remote_localhost_exec_lock",
        base_label="remote_localhost",
        path=path,
        timeout=10,
    )

    selected = frontdoor if isinstance(frontdoor.payload, dict) else remote_localhost
    payload = selected.payload if isinstance(selected.payload, dict) else {}

    has_active_key = isinstance(payload, dict) and "active_run_id" in payload
    locked = bool(payload.get("locked")) if isinstance(payload, dict) else False
    active = payload.get("active_run_id") if isinstance(payload, dict) else None

    schema_ok = has_active_key and isinstance(payload.get("locked"), bool)
    if locked:
        schema_ok = schema_ok and isinstance(active, str) and bool(active.strip())

    parse_ok = synthetic.get("state") == "ALREADY_RUNNING" and synthetic.get("run_id") == "run-abc123"

    details = {
        "synthetic_parse_ok": parse_ok,
        "selected_source": selected.base_label,
        "selected_http_code": selected.http_code,
        "frontdoor_http_code": frontdoor.http_code,
        "remote_localhost_http_code": remote_localhost.http_code,
        "status_endpoint_has_active_run_id": has_active_key,
        "status_endpoint_schema_ok": schema_ok,
        "locked": locked,
        "active_run_id": active,
    }

    if parse_ok and schema_ok:
        return builder.finalize(
            status="PASS",
            message="concurrency semantics PASS (ALREADY_RUNNING parse + lock status schema)",
            details=details,
        )

    return builder.finalize(
        status="FAIL",
        message="concurrency semantics FAIL",
        error_class="CONCURRENCY_SCHEMA_FAIL",
        details=details,
    )


def check_truthful_status_fields(builder: CheckBuilder, runtime: MatrixRuntime):
    frontdoor = builder.request(
        label="frontdoor_hostd_truthful",
        base_label="frontdoor",
        path="/api/host-executor/status",
        timeout=10,
    )
    remote_localhost = builder.request(
        label="remote_localhost_hostd_truthful",
        base_label="remote_localhost",
        path="/api/host-executor/status",
        timeout=10,
    )

    selected = frontdoor if isinstance(frontdoor.payload, dict) else remote_localhost
    payload = selected.payload if isinstance(selected.payload, dict) else None

    project_path = "/api/projects/soma_kajabi/status"
    project_status = builder.request(
        label="frontdoor_project_status_soma",
        base_label="frontdoor",
        path=project_path,
        timeout=10,
    )

    truthful_ok = False
    details: dict[str, Any] = {
        "selected_source": selected.base_label,
        "selected_http_code": selected.http_code,
        "frontdoor_http_code": frontdoor.http_code,
        "remote_localhost_http_code": remote_localhost.http_code,
        "project_status_http_code": project_status.http_code,
        "schema_checks": {},
    }

    if isinstance(payload, dict):
        has_ok = isinstance(payload.get("ok"), bool)
        hostd_status = str(payload.get("hostd_status") or "").strip().lower()
        can_reach = payload.get("console_can_reach_hostd")
        has_can_reach = isinstance(can_reach, bool)

        truthful = False
        if has_ok and has_can_reach:
            if payload.get("ok") is True:
                truthful = hostd_status == "up" and can_reach is True
            else:
                has_error_hint = bool(payload.get("error_class") or payload.get("last_error_redacted"))
                truthful = hostd_status == "down" and can_reach is False and has_error_hint

        details["schema_checks"] = {
            "has_ok_bool": has_ok,
            "has_console_can_reach_hostd_bool": has_can_reach,
            "hostd_status": hostd_status,
            "truthful": truthful,
        }
        truthful_ok = truthful
    else:
        details["schema_checks"] = {"payload_is_object": False}
        truthful_ok = False

    if project_status.http_code == 200 and isinstance(project_status.payload, dict):
        mirror_state = str(project_status.payload.get("mirror_state") or "")
        mirror_pass = project_status.payload.get("mirror_pass")
        project_truthful = not mirror_state.startswith("UNKNOWN") or mirror_pass is None
        details["project_status_checks"] = {
            "active_run_id_key_present": "active_run_id" in project_status.payload,
            "mirror_state": mirror_state,
            "mirror_pass": mirror_pass,
            "truthful": project_truthful,
        }
        truthful_ok = truthful_ok and project_truthful
    elif project_status.http_code not in (404, -1, 0):
        details["project_status_checks"] = {
            "error": "unexpected_http_code",
            "http_code": project_status.http_code,
        }
        truthful_ok = False

    if truthful_ok:
        return builder.finalize(
            status="PASS",
            message="status fields are truthful (no optimistic defaults detected)",
            details=details,
        )

    return builder.finalize(
        status="FAIL",
        message="status fields failed truthful schema checks",
        error_class="TRUTHFUL_STATUS_FIELDS_FAIL",
        details=details,
    )


def core_platform_checks() -> list[CheckSpec]:
    return [
        CheckSpec(id="CORE.HQ_HEALTH", scope="core", project=None, handler=check_hq_health),
        CheckSpec(id="CORE.HOSTD_STATUS", scope="core", project=None, handler=check_hostd_status),
        CheckSpec(id="CORE.BROWSE_TRANSPORT", scope="core", project=None, handler=check_browse_transport),
        CheckSpec(id="CORE.CONCURRENCY_SEMANTICS", scope="core", project=None, handler=check_concurrency_semantics),
        CheckSpec(id="CORE.TRUTHFUL_STATUS_FIELDS", scope="core", project=None, handler=check_truthful_status_fields),
    ]
