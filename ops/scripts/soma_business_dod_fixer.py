#!/usr/bin/env python3
"""Business DoD interactive executor for targeted Kajabi UI fixes.

Flow:
1) verify_business_dod (before)
2) if failing checks include privacy/raw targets, run authenticated UI executor
3) curl-check /terms and /privacy-policy
4) verify_business_dod (after)
5) emit RESULT.json + proof artifacts
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Ensure repo root on path
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from ops.system.soma_preflight import run_soma_preflight  # noqa: E402
from services.soma_kajabi.kajabi_ui_fixer import (  # noqa: E402
    collect_curl_checks,
    run_business_dod_ui_fixes,
)
from services.soma_kajabi.verify_business_dod import verify_business_dod  # noqa: E402


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _artifacts_root() -> Path:
    env = os.environ.get("OPENCLAW_ARTIFACTS_ROOT")
    if env:
        return Path(env)
    if Path("/opt/ai-ops-runner/artifacts").is_dir():
        return Path("/opt/ai-ops-runner/artifacts")
    return _REPO_ROOT / "artifacts"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _failing_checks(result: dict[str, Any]) -> list[str]:
    checks = result.get("checks") if isinstance(result, dict) else {}
    if not isinstance(checks, dict):
        return []
    out: list[str] = []
    for name, check in checks.items():
        if not isinstance(check, dict):
            continue
        status = str(check.get("status") or "").upper()
        passed = bool(check.get("pass"))
        if status == "FAIL" or (not passed and status != "WARN"):
            out.append(str(name))
    return sorted(set(out))


def _count_by_status(result: dict[str, Any]) -> tuple[int, int]:
    checks = result.get("checks") if isinstance(result, dict) else {}
    fail_count = 0
    warn_count = 0
    if isinstance(checks, dict):
        for check in checks.values():
            if not isinstance(check, dict):
                continue
            status = str(check.get("status") or "").upper()
            if status == "FAIL":
                fail_count += 1
            if status == "WARN":
                warn_count += 1
    return fail_count, warn_count


def _latest_business_dod_pointer(artifacts_root: Path) -> dict[str, Any] | None:
    path = artifacts_root / "soma_kajabi" / "business_dod" / "LATEST.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None
    except Exception as e:
        return {"error": f"latest_pointer_unavailable:{e}"}


def main() -> int:
    artifacts_root = _artifacts_root()
    run_id = f"fixer_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{os.getpid()}"
    out_dir = artifacts_root / "soma_kajabi" / "business_dod_fixer" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    skip_network = os.environ.get("OPENCLAW_BUSINESS_DOD_SKIP_NETWORK", "").lower() in ("1", "true", "yes")

    before = verify_business_dod(artifacts_root=artifacts_root, skip_network_checks=skip_network)
    _write_json(out_dir / "business_dod_before.json", before)

    failing = _failing_checks(before)
    need_privacy_fix = "terms_privacy_urls" in failing
    need_raw_fix = "raw_module_present" in failing
    targeted_failures = [x for x in failing if x in {"terms_privacy_urls", "raw_module_present"}]
    ui_fix_result: dict[str, Any] = {"status": "SKIP", "reason": "NO_TARGETED_FAILURES"}
    snapshot_path: Path | None = None
    preflight_payload: dict[str, Any] | None = None

    if targeted_failures:
        hq_base = os.environ.get("OPENCLAW_HQ_BASE", "http://127.0.0.1:8787")
        preflight_payload = run_soma_preflight(
            artifacts_root=artifacts_root,
            hq_base=hq_base,
            run_id=f"{run_id}_preflight",
            mock=False,
        )
        _write_json(out_dir / "soma_preflight.json", preflight_payload)
        preflight_status = str(preflight_payload.get("status") or "")
        if preflight_status == "HUMAN_ONLY":
            result = {
                "status": "HUMAN_ONLY",
                "ok": False,
                "pass": False,
                "run_id": run_id,
                "created_at": _now_iso(),
                "artifact_dir": str(out_dir),
                "error_class": "WAITING_FOR_HUMAN",
                "message": "Preflight reported HUMAN_ONLY.",
                "novnc_url": preflight_payload.get("novnc_url"),
                "gate_expiry": preflight_payload.get("gate_expiry"),
                "instruction": "log in + 2FA, then CLOSE noVNC to release lock",
                "failing_checks_before": failing,
            }
            _write_json(out_dir / "RESULT.json", result)
            print(json.dumps(result, indent=2))
            return 0
        if preflight_status != "GO":
            result = {
                "status": "FAIL",
                "ok": False,
                "pass": False,
                "run_id": run_id,
                "created_at": _now_iso(),
                "artifact_dir": str(out_dir),
                "error_class": "SOMA_PREFLIGHT_NO_GO",
                "message": f"Preflight status={preflight_status}",
                "reasons": preflight_payload.get("reasons"),
                "failing_checks_before": failing,
            }
            _write_json(out_dir / "RESULT.json", result)
            print(json.dumps(result, indent=2))
            return 1

        ui_fix_result = run_business_dod_ui_fixes(
            artifact_dir=out_dir,
            run_id=run_id,
            need_privacy_fix=need_privacy_fix,
            need_raw_fix=need_raw_fix,
            max_llm_calls=int(os.environ.get("OPENCLAW_BDOD_FIXER_MAX_LLM_CALLS", "1")),
            max_steps=int(os.environ.get("OPENCLAW_BDOD_FIXER_MAX_STEPS", "6")),
        )
        _write_json(out_dir / "ui_fix_result.json", ui_fix_result)
        if isinstance(ui_fix_result.get("snapshot_path"), str) and ui_fix_result.get("snapshot_path"):
            snapshot_path = Path(str(ui_fix_result["snapshot_path"]))

        if ui_fix_result.get("status") == "HUMAN_ONLY":
            result = {
                "status": "HUMAN_ONLY",
                "ok": False,
                "pass": False,
                "run_id": run_id,
                "created_at": _now_iso(),
                "artifact_dir": str(out_dir),
                "error_class": ui_fix_result.get("error_class"),
                "message": ui_fix_result.get("message"),
                "novnc_url": ui_fix_result.get("novnc_url"),
                "gate_expiry": ui_fix_result.get("gate_expiry"),
                "instruction": ui_fix_result.get("instruction"),
                "failing_checks_before": failing,
            }
            _write_json(out_dir / "RESULT.json", result)
            print(json.dumps(result, indent=2))
            return 0

    curl_checks = collect_curl_checks()
    _write_json(out_dir / "curl_checks.json", curl_checks)

    after = verify_business_dod(
        artifacts_root=artifacts_root,
        skip_network_checks=skip_network,
        snapshot_path=snapshot_path,
    )
    _write_json(out_dir / "business_dod_after.json", after)

    fail_count, warn_count = _count_by_status(after)
    latest_pointer = _latest_business_dod_pointer(artifacts_root)
    after_summary = {
        "run_id": run_id,
        "status": "PASS" if after.get("pass") else "FAIL",
        "fail_count": fail_count,
        "warn_count": warn_count,
        "latest_pointer": latest_pointer,
    }
    _write_json(out_dir / "business_dod_after_summary.json", after_summary)

    if ui_fix_result.get("status") == "FAIL":
        result = {
            "status": "FAIL",
            "ok": False,
            "pass": False,
            "run_id": run_id,
            "created_at": _now_iso(),
            "artifact_dir": str(out_dir),
            "error_class": ui_fix_result.get("error_class") or "BDOD_UI_FIX_FAILED",
            "message": ui_fix_result.get("message") or "UI fixer failed.",
            "failing_checks_before": failing,
            "failing_checks_after": _failing_checks(after),
            "curl_checks": curl_checks,
        }
        _write_json(out_dir / "RESULT.json", result)
        print(json.dumps(result, indent=2))
        return 1

    if before.get("pass") and not targeted_failures:
        final_status = "PASS"
        final_ok = True
        final_msg = "Business DoD already PASS."
    elif after.get("pass"):
        final_status = "PASS"
        final_ok = True
        final_msg = "Business DoD PASS after interactive fixes."
    else:
        final_status = "FAIL"
        final_ok = False
        final_msg = "Business DoD still failing after fixer run."

    result = {
        "status": final_status,
        "ok": final_ok,
        "pass": bool(after.get("pass")),
        "run_id": run_id,
        "created_at": _now_iso(),
        "artifact_dir": str(out_dir),
        "business_dod_before_run_id": before.get("run_id"),
        "business_dod_after_run_id": after.get("run_id"),
        "failing_checks_before": failing,
        "failing_checks_after": _failing_checks(after),
        "targeted_failures": targeted_failures,
        "ui_fix_result": ui_fix_result,
        "curl_checks": curl_checks,
        "message": final_msg,
    }
    _write_json(out_dir / "RESULT.json", result)
    print(json.dumps(result, indent=2))
    return 0 if final_status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
