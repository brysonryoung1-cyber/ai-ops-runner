"""Core artifact-root and browse-shape checks for doctor matrix."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ..parsers import detect_browse_pagination
from ..runtime import CheckBuilder, CheckSpec, MatrixRuntime


def _get_entries_count(payload: Any) -> int | None:
    if isinstance(payload, dict) and isinstance(payload.get("entries"), list):
        return len(payload.get("entries", []))
    return None


def check_browse_pagination_detected(builder: CheckBuilder, runtime: MatrixRuntime):
    primary_path = "/api/artifacts/browse?path=review_packets"
    fallback_path = "/api/artifacts/browse?path="

    frontdoor = builder.request(
        label="frontdoor_browse_review_packets",
        base_label="frontdoor",
        path=primary_path,
        timeout=10,
    )

    localhost = builder.request(
        label="localhost_browse_review_packets",
        base_label="localhost",
        path=primary_path,
        timeout=10,
    )

    selected = None
    selected_path = primary_path

    if frontdoor.http_code == 200 and isinstance(frontdoor.payload, dict):
        selected = frontdoor
    elif localhost.http_code == 200 and isinstance(localhost.payload, dict):
        selected = localhost
    else:
        frontdoor_fallback = builder.request(
            label="frontdoor_browse_root_fallback",
            base_label="frontdoor",
            path=fallback_path,
            timeout=10,
        )
        if frontdoor_fallback.http_code == 200 and isinstance(frontdoor_fallback.payload, dict):
            selected = frontdoor_fallback
            selected_path = fallback_path

    payload = selected.payload if selected is not None else None
    parsed = detect_browse_pagination(payload)
    builder.write_json("browse_pagination_detection.json", parsed)

    details = {
        "requested_path": primary_path,
        "selected_path": selected_path,
        "frontdoor_http_code": frontdoor.http_code,
        "localhost_http_code": localhost.http_code,
        "selected_source": selected.base_label if selected is not None else None,
        "selected_http_code": selected.http_code if selected is not None else None,
        "frontdoor_entries_count": _get_entries_count(frontdoor.payload),
        "localhost_entries_count": _get_entries_count(localhost.payload),
        "pagination_detection": parsed,
    }

    if not parsed.get("parse_ok"):
        return builder.finalize(
            status="FAIL",
            message="browse payload could not be parsed for pagination detection",
            error_class="BROWSE_PAGINATION_PARSE_FAIL",
            details=details,
        )

    if parsed.get("likely_capped") and not parsed.get("has_pagination_fields"):
        return builder.finalize(
            status="FAIL",
            message="browse appears capped without pagination fields",
            error_class="BROWSE_CAPPED_NO_PAGINATION",
            details=details,
        )

    return builder.finalize(
        status="PASS",
        message="browse pagination contract detected",
        details=details,
    )


def check_artifacts_root_canonical(builder: CheckBuilder, runtime: MatrixRuntime):
    env_root = os.environ.get("OPENCLAW_ARTIFACTS_ROOT", "").strip() or None
    vps_root = Path("/opt/ai-ops-runner/artifacts")
    repo_root = runtime.repo_root / "artifacts"
    canonical = runtime.artifacts_root

    def _describe(path: Path) -> dict[str, Any]:
        exists = path.exists()
        is_dir = path.is_dir()
        sample: list[str] = []
        if exists and is_dir:
            try:
                sample = sorted([p.name for p in path.iterdir()])[:25]
            except OSError:
                sample = []
        return {
            "path": str(path),
            "exists": exists,
            "is_dir": is_dir,
            "readable": os.access(path, os.R_OK),
            "writable": os.access(path, os.W_OK),
            "executable": os.access(path, os.X_OK),
            "sample_entries": sample,
        }

    candidates = {
        "env": _describe(Path(env_root)) if env_root else None,
        "vps_preferred": _describe(vps_root),
        "repo_fallback": _describe(repo_root),
        "canonical": _describe(canonical),
    }
    builder.write_json("artifacts_root_probe.json", candidates)

    canonical_probe = candidates["canonical"] or {}
    pass_ok = bool(
        canonical_probe.get("exists")
        and canonical_probe.get("is_dir")
        and canonical_probe.get("readable")
        and canonical_probe.get("executable")
    )

    details = {
        "env_override": env_root,
        "canonical_path": str(canonical),
        "probes": candidates,
    }

    if pass_ok:
        return builder.finalize(
            status="PASS",
            message=f"canonical artifacts root resolved to {canonical}",
            details=details,
        )

    return builder.finalize(
        status="FAIL",
        message=f"canonical artifacts root invalid: {canonical}",
        error_class="ARTIFACTS_ROOT_INVALID",
        details=details,
    )


def check_run_dir_resolution_contract(builder: CheckBuilder, runtime: MatrixRuntime):
    contracts = list(runtime.run_dir_contracts)
    contract_results: list[dict[str, Any]] = []

    if not contracts:
        builder.write_json("run_dir_contracts.json", {"contracts": [], "note": "no project contracts declared"})
        return builder.finalize(
            status="PASS",
            message="no run-dir contracts declared by enabled plugins",
            details={"contracts_checked": 0},
        )

    all_ok = True
    for contract in contracts:
        pointer_path = runtime.artifacts_root / contract.pointer_relpath
        record: dict[str, Any] = {
            "project": contract.project,
            "pointer_relpath": contract.pointer_relpath,
            "pointer_path": str(pointer_path),
            "required_fields": list(contract.required_fields),
            "run_dir_field": contract.run_dir_field,
            "pointer_exists": pointer_path.exists(),
            "pointer_parse_ok": False,
            "fields_ok": False,
            "run_dir_exists": False,
        }

        payload: dict[str, Any] | None = None
        if pointer_path.exists() and pointer_path.is_file():
            try:
                parsed = json.loads(pointer_path.read_text(encoding="utf-8"))
                if isinstance(parsed, dict):
                    payload = parsed
                    record["pointer_parse_ok"] = True
            except (OSError, json.JSONDecodeError):
                payload = None

        if isinstance(payload, dict):
            missing = [
                field_name
                for field_name in contract.required_fields
                if not isinstance(payload.get(field_name), str) or not str(payload.get(field_name)).strip()
            ]
            record["missing_fields"] = missing
            fields_ok = len(missing) == 0
            record["fields_ok"] = fields_ok
            run_dir_val = payload.get(contract.run_dir_field)
            if isinstance(run_dir_val, str) and run_dir_val.strip():
                run_dir_path = pointer_path.parent / run_dir_val
                record["run_dir_path"] = str(run_dir_path)
                record["run_dir_exists"] = run_dir_path.is_dir()
            else:
                record["run_dir_path"] = None
                record["run_dir_exists"] = False
        else:
            record["missing_fields"] = list(contract.required_fields)

        contract_ok = bool(
            record.get("pointer_exists")
            and record.get("pointer_parse_ok")
            and record.get("fields_ok")
            and record.get("run_dir_exists")
        )
        record["contract_ok"] = contract_ok
        all_ok = all_ok and contract_ok
        contract_results.append(record)

    builder.write_json("run_dir_contracts.json", {"contracts": contract_results})

    details = {
        "contracts_checked": len(contract_results),
        "contracts": contract_results,
    }

    if all_ok:
        return builder.finalize(
            status="PASS",
            message="run-dir resolution contracts are deterministic and valid",
            details=details,
        )

    return builder.finalize(
        status="FAIL",
        message="one or more run-dir resolution contracts failed",
        error_class="RUN_DIR_CONTRACT_INVALID",
        details=details,
    )


def core_artifact_checks() -> list[CheckSpec]:
    return [
        CheckSpec(
            id="CORE.BROWSE_PAGINATION_DETECTED",
            scope="core",
            project=None,
            handler=check_browse_pagination_detected,
        ),
        CheckSpec(
            id="CORE.ARTIFACTS_ROOT_CANONICAL",
            scope="core",
            project=None,
            handler=check_artifacts_root_canonical,
        ),
        CheckSpec(
            id="CORE.RUN_DIR_RESOLUTION_CONTRACT",
            scope="core",
            project=None,
            handler=check_run_dir_resolution_contract,
        ),
    ]
