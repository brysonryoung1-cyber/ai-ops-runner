"""Doctor matrix plugin for soma_kajabi project."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from ops.lib.doctor_matrix.models import RunDirContract
from ops.lib.doctor_matrix.parsers import (
    parse_json_content_from_browse_response,
    parse_pointer_from_browse_response,
)
from ops.lib.doctor_matrix.runtime import CheckBuilder, CheckSpec, MatrixRuntime

POINTER_REL_PATH = "soma_kajabi/run_to_done/LATEST_RUN.json"


@dataclass
class _BrowseFetchResult:
    selected_source: str
    selected_http_code: int
    frontdoor_http_code: int
    remote_localhost_http_code: int
    response_body: str


def _fetch_browse_file(
    builder: CheckBuilder,
    rel_path: str,
    *,
    label_prefix: str,
) -> _BrowseFetchResult:
    path = f"/api/artifacts/browse?path={quote(rel_path, safe='/')}"
    frontdoor = builder.request(
        label=f"{label_prefix}_frontdoor",
        base_label="frontdoor",
        path=path,
        timeout=10,
    )

    remote_localhost = builder.request(
        label=f"{label_prefix}_remote_localhost",
        base_label="remote_localhost",
        path=path,
        timeout=10,
    )

    selected = frontdoor if frontdoor.http_code == 200 else remote_localhost
    return _BrowseFetchResult(
        selected_source=selected.base_label,
        selected_http_code=selected.http_code,
        frontdoor_http_code=frontdoor.http_code,
        remote_localhost_http_code=remote_localhost.http_code,
        response_body=selected.body_text,
    )


def _pointer_required_fields(pointer: dict[str, Any]) -> tuple[bool, list[str]]:
    missing = [
        key
        for key in ("run_id", "run_dir", "status")
        if not isinstance(pointer.get(key), str) or not str(pointer.get(key)).strip()
    ]
    return len(missing) == 0, missing


def check_soma_pointer_present(builder: CheckBuilder, runtime: MatrixRuntime):
    fetched = _fetch_browse_file(
        builder,
        POINTER_REL_PATH,
        label_prefix="pointer_latest_run",
    )

    pointer_payload = parse_pointer_from_browse_response(fetched.response_body)
    builder.write_json(
        "pointer_parsed.json",
        {
            "pointer": pointer_payload,
            "selected_source": fetched.selected_source,
            "selected_http_code": fetched.selected_http_code,
        },
    )

    details = {
        "pointer_rel_path": POINTER_REL_PATH,
        "selected_source": fetched.selected_source,
        "selected_http_code": fetched.selected_http_code,
        "frontdoor_http_code": fetched.frontdoor_http_code,
        "remote_localhost_http_code": fetched.remote_localhost_http_code,
    }

    if fetched.selected_http_code != 200:
        return builder.finalize(
            status="FAIL",
            message=f"LATEST_RUN pointer browse failed (HTTP {fetched.selected_http_code})",
            error_class="SOMA_POINTER_HTTP_FAIL",
            details=details,
        )

    if not isinstance(pointer_payload, dict):
        return builder.finalize(
            status="FAIL",
            message="LATEST_RUN pointer content is missing or invalid JSON",
            error_class="SOMA_POINTER_PARSE_FAIL",
            details=details,
        )

    fields_ok, missing = _pointer_required_fields(pointer_payload)
    details["missing_fields"] = missing
    details["pointer_status"] = pointer_payload.get("status")

    if not fields_ok:
        return builder.finalize(
            status="FAIL",
            message="LATEST_RUN pointer missing required fields",
            error_class="SOMA_POINTER_FIELDS_MISSING",
            details=details,
        )

    return builder.finalize(
        status="PASS",
        message="LATEST_RUN pointer is fetchable and parseable",
        details=details,
    )


def _shape_ok_for_proof(proof: dict[str, Any]) -> tuple[bool, str | None]:
    status = proof.get("status")
    if not isinstance(status, str) or not status.strip():
        return False, "status_missing"
    normalized = status.strip().upper()
    if normalized in {"FAIL", "FAILURE", "TIMEOUT"}:
        err = proof.get("error_class")
        if not isinstance(err, str) or not err.strip():
            return False, "error_class_missing_for_fail_status"
    return True, None


def _shape_ok_for_precheck(precheck: dict[str, Any]) -> tuple[bool, str | None]:
    status = precheck.get("status")
    if not isinstance(status, str) or not status.strip():
        return False, "status_missing"
    if status.strip().upper() == "FAIL":
        err = precheck.get("error_class")
        if not isinstance(err, str) or not err.strip():
            return False, "error_class_missing_for_fail_status"
    return True, None


def check_soma_run_to_done_proof_shape(builder: CheckBuilder, runtime: MatrixRuntime):
    pointer_fetch = _fetch_browse_file(
        builder,
        POINTER_REL_PATH,
        label_prefix="shape_pointer_latest_run",
    )
    pointer_payload = parse_pointer_from_browse_response(pointer_fetch.response_body)

    details: dict[str, Any] = {
        "pointer_rel_path": POINTER_REL_PATH,
        "pointer_http_code": pointer_fetch.selected_http_code,
        "pointer_source": pointer_fetch.selected_source,
    }

    if pointer_fetch.selected_http_code != 200 or not isinstance(pointer_payload, dict):
        return builder.finalize(
            status="FAIL",
            message="cannot validate proof shape without parseable LATEST_RUN pointer",
            error_class="SOMA_POINTER_UNAVAILABLE",
            details=details,
        )

    run_dir = pointer_payload.get("run_dir")
    if not isinstance(run_dir, str) or not run_dir.strip():
        return builder.finalize(
            status="FAIL",
            message="LATEST_RUN pointer missing run_dir",
            error_class="SOMA_POINTER_RUN_DIR_MISSING",
            details=details,
        )

    proof_rel = f"soma_kajabi/run_to_done/{run_dir}/PROOF.json"
    precheck_rel = f"soma_kajabi/run_to_done/{run_dir}/PRECHECK.json"

    proof_fetch = _fetch_browse_file(
        builder,
        proof_rel,
        label_prefix="shape_proof",
    )
    precheck_fetch = _fetch_browse_file(
        builder,
        precheck_rel,
        label_prefix="shape_precheck",
    )

    proof_payload = parse_json_content_from_browse_response(proof_fetch.response_body)
    precheck_payload = parse_json_content_from_browse_response(precheck_fetch.response_body)

    builder.write_json(
        "shape_parsed_payloads.json",
        {
            "pointer": pointer_payload,
            "proof": proof_payload,
            "precheck": precheck_payload,
        },
    )

    details.update(
        {
            "run_dir": run_dir,
            "proof_rel": proof_rel,
            "precheck_rel": precheck_rel,
            "proof_http_code": proof_fetch.selected_http_code,
            "precheck_http_code": precheck_fetch.selected_http_code,
        }
    )

    if proof_fetch.selected_http_code != 200 or not isinstance(proof_payload, dict):
        return builder.finalize(
            status="FAIL",
            message="PROOF.json missing or invalid via browse",
            error_class="SOMA_PROOF_UNAVAILABLE",
            details=details,
        )

    if precheck_fetch.selected_http_code != 200 or not isinstance(precheck_payload, dict):
        return builder.finalize(
            status="FAIL",
            message="PRECHECK.json missing or invalid via browse",
            error_class="SOMA_PRECHECK_UNAVAILABLE",
            details=details,
        )

    proof_ok, proof_err = _shape_ok_for_proof(proof_payload)
    precheck_ok, precheck_err = _shape_ok_for_precheck(precheck_payload)
    details["proof_shape_ok"] = proof_ok
    details["precheck_shape_ok"] = precheck_ok
    details["proof_shape_error"] = proof_err
    details["precheck_shape_error"] = precheck_err

    if proof_ok and precheck_ok:
        return builder.finalize(
            status="PASS",
            message="run_to_done PROOF/PRECHECK schema shape is valid",
            details=details,
        )

    return builder.finalize(
        status="FAIL",
        message="run_to_done PROOF/PRECHECK schema shape invalid",
        error_class="SOMA_PROOF_SHAPE_INVALID",
        details=details,
    )


class SomaKajabiDoctorPlugin:
    name = "soma_kajabi"

    def enabled(self, runtime: MatrixRuntime) -> bool:
        return True

    def run_dir_contracts(self, runtime: MatrixRuntime) -> list[RunDirContract]:
        return [RunDirContract(project=self.name, pointer_relpath=POINTER_REL_PATH)]

    def checks(self, runtime: MatrixRuntime) -> list[CheckSpec]:
        return [
            CheckSpec(
                id="PROJECT.SOMA_POINTER_PRESENT",
                scope="project",
                project=self.name,
                handler=check_soma_pointer_present,
            ),
            CheckSpec(
                id="PROJECT.SOMA_RUN_TO_DONE_PROOF_SHAPE",
                scope="project",
                project=self.name,
                handler=check_soma_run_to_done_proof_shape,
            ),
        ]


def get_plugin() -> SomaKajabiDoctorPlugin:
    return SomaKajabiDoctorPlugin()


PLUGIN = SomaKajabiDoctorPlugin()
