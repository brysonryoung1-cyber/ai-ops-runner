#!/usr/bin/env python3
"""Always-on deterministic brain loop (0-LLM)."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import traceback
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ops.lib.notifier import build_alert_hash, send_discord_webhook_alert

DEFAULT_STATE_ROOT = Path("/opt/ai-ops-runner/state/brain_loop")
DEFAULT_ARTIFACTS_ROOT = Path("/opt/ai-ops-runner/artifacts")
TRACEBACK_MAX_LINES = 200


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_run_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"brain_loop_{ts}_{secrets.token_hex(4)}"


def normalize_failed_checks(items: list[str] | None) -> list[str]:
    values = [str(item).strip() for item in (items or []) if str(item).strip()]
    return sorted(set(values))


def parse_bool_env(name: str, default: bool) -> bool:
    value = os.environ.get(name, "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=str(path.parent), delete=False) as tmp:
        tmp.write(content)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    atomic_write_text(path, json.dumps(payload, indent=2) + "\n")


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def resolve_artifacts_root(value: str) -> Path:
    if value:
        return Path(value).expanduser()
    if DEFAULT_ARTIFACTS_ROOT.exists():
        return DEFAULT_ARTIFACTS_ROOT
    return REPO_ROOT / "artifacts"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OpenClaw deterministic brain loop")
    parser.add_argument("--mode", choices=("all", "core"), default="all")
    parser.add_argument("--mock", action="store_true", help="Use injected matrix result (no doctor execution)")
    parser.add_argument("--mock-status", choices=("PASS", "FAIL"), default="PASS")
    parser.add_argument(
        "--mock-failed-checks",
        default="",
        help="Comma-separated check ids for --mock-status FAIL",
    )
    parser.add_argument(
        "--state-root",
        default=str(DEFAULT_STATE_ROOT),
        help="State root directory (default /opt/ai-ops-runner/state/brain_loop)",
    )
    parser.add_argument(
        "--artifacts-root",
        default="",
        help="Artifacts root override",
    )
    return parser.parse_args(argv)


@contextmanager
def temporary_env(values: dict[str, str]):
    previous: dict[str, str | None] = {}
    for key, value in values.items():
        previous[key] = os.environ.get(key)
        os.environ[key] = value
    try:
        yield
    finally:
        for key, old in previous.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old


def decide_event(
    *,
    prev_state: dict[str, Any] | None,
    matrix_status: str,
    failed_checks: list[str],
    alert_on_first_fail: bool,
) -> tuple[str | None, bool]:
    matrix_status = "FAIL" if matrix_status != "PASS" else "PASS"
    failed_checks = normalize_failed_checks(failed_checks)
    if not prev_state:
        if matrix_status == "FAIL" and alert_on_first_fail:
            return "FIRST_FAIL", True
        return None, False

    prev_status = "FAIL" if str(prev_state.get("matrix_status")) != "PASS" else "PASS"
    prev_failed = normalize_failed_checks(prev_state.get("failed_checks") or [])

    if prev_status == "PASS" and matrix_status == "FAIL":
        return "PASS_TO_FAIL", True
    if prev_status == "FAIL" and matrix_status == "PASS":
        return "FAIL_TO_PASS", True
    if prev_status == "FAIL" and matrix_status == "FAIL" and prev_failed != failed_checks:
        return "FAIL_CHECKS_CHANGED", True
    return None, False


def _parse_doctor_stdout(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if not text:
        raise ValueError("doctor subprocess emitted empty stdout")
    try:
        payload = json.loads(text)
        if isinstance(payload, dict):
            return payload
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("doctor subprocess emitted non-json output")
    payload = json.loads(text[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("doctor subprocess json was not an object")
    return payload


def _run_doctor_live(mode: str, artifacts_root: Path) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    env = {
        "OPENCLAW_REPO_ROOT": str(REPO_ROOT),
        "OPENCLAW_ARTIFACTS_ROOT": str(artifacts_root),
    }
    argv = ["--mode", mode]

    try:
        from ops.system.doctor_matrix import run_doctor_matrix

        with temporary_env(env):
            _exit_code, payload = run_doctor_matrix(argv)
        if not isinstance(payload, dict):
            raise RuntimeError("doctor in-process returned invalid payload")
        return payload, warnings
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"doctor_inprocess_failed:{type(exc).__name__}")

    command = [sys.executable, str(REPO_ROOT / "ops" / "system" / "doctor_matrix.py"), "--mode", mode]
    proc = subprocess.run(
        command,
        capture_output=True,
        text=True,
        env={**os.environ, **env},
        timeout=360,
        check=False,
    )
    payload = _parse_doctor_stdout(proc.stdout)
    return payload, warnings


def run_matrix(
    *,
    mode: str,
    mock: bool,
    mock_status: str,
    mock_failed_checks: list[str],
    artifacts_root: Path,
    brain_bundle_dir: Path,
) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    if mock:
        failed_checks = normalize_failed_checks(mock_failed_checks if mock_status == "FAIL" else [])
        matrix_run_id = f"doctor_matrix_mock_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        matrix_bundle_dir = brain_bundle_dir / "doctor_matrix_mock"
        matrix_bundle_dir.mkdir(parents=True, exist_ok=True)
        matrix_result = {
            "run_id": matrix_run_id,
            "status": mock_status,
            "failed_checks": failed_checks,
            "bundle_dir": str(matrix_bundle_dir),
        }
        atomic_write_json(matrix_bundle_dir / "RESULT.json", matrix_result)
        return matrix_result, warnings

    payload, run_warnings = _run_doctor_live(mode, artifacts_root)
    warnings.extend(run_warnings)
    status = "PASS" if str(payload.get("status")) == "PASS" else "FAIL"
    failed_checks = normalize_failed_checks(payload.get("failed_checks") or [])
    matrix_bundle_dir = str(payload.get("bundle_dir") or "")
    if not matrix_bundle_dir:
        raise RuntimeError("doctor payload missing bundle_dir")
    result_path = Path(matrix_bundle_dir) / "RESULT.json"
    if result_path.exists():
        result_payload = read_json(result_path)
        if isinstance(result_payload, dict):
            status = "PASS" if str(result_payload.get("status")) == "PASS" else "FAIL"
            failed_checks = normalize_failed_checks(result_payload.get("failed_checks") or [])
            payload = result_payload
            payload["bundle_dir"] = matrix_bundle_dir
    payload["status"] = status
    payload["failed_checks"] = failed_checks
    payload["bundle_dir"] = matrix_bundle_dir
    return payload, warnings


def build_summary(result: dict[str, Any]) -> str:
    event_type = result.get("event_type") or "NONE"
    failed_checks = result.get("failed_checks") or []
    failed_count = len(failed_checks) if isinstance(failed_checks, list) else 0
    lines = [
        f"# Brain Loop — {result.get('run_id')}",
        "",
        f"- Status: **{result.get('status')}**",
        f"- Matrix status: `{result.get('matrix_status')}`",
        f"- Event type: `{event_type}`",
        f"- Failed checks count: `{failed_count}`",
        f"- Alert sent: `{result.get('alert_sent')}`",
        f"- Brain bundle: `{result.get('bundle_dir')}`",
        f"- Doctor matrix bundle: `{result.get('doctor_matrix_bundle_dir')}`",
    ]
    warnings = result.get("warnings") or []
    if isinstance(warnings, list) and warnings:
        lines.extend(["", "## Warnings"])
        lines.extend([f"- {w}" for w in warnings])
    return "\n".join(lines) + "\n"


def format_alert_message(
    *,
    event_type: str,
    matrix_status: str,
    failed_checks: list[str],
    brain_bundle_dir: Path,
    doctor_bundle_dir: Path,
) -> str:
    failed_preview = failed_checks[:8]
    failed_preview_text = ", ".join(f"`{item}`" for item in failed_preview) if failed_preview else "(none)"
    extra = ""
    if len(failed_checks) > 8:
        extra = f" (+{len(failed_checks) - 8} more)"
    return (
        "OpenClaw Brain Loop alert\n"
        f"event_type: {event_type}\n"
        f"matrix_status: {matrix_status}\n"
        f"failed_checks_count: {len(failed_checks)}\n"
        f"failed_checks: {failed_preview_text}{extra}\n"
        f"brain_proof: {brain_bundle_dir}\n"
        f"doctor_proof: {doctor_bundle_dir}"
    )


def _bounded_traceback(trace: str) -> str:
    lines = (trace or "").splitlines()
    if len(lines) <= TRACEBACK_MAX_LINES:
        return "\n".join(lines) + ("\n" if lines else "")
    trimmed = lines[-TRACEBACK_MAX_LINES:]
    return "\n".join(trimmed) + "\n"


def execute(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    run_id = build_run_id()
    artifacts_root = resolve_artifacts_root(args.artifacts_root)
    state_root = Path(args.state_root).expanduser()
    bundle_dir = artifacts_root / "system" / "brain_loop" / run_id
    bundle_dir.mkdir(parents=True, exist_ok=True)

    result: dict[str, Any] = {
        "ok": False,
        "status": "FAIL",
        "run_id": run_id,
        "started_at": now_utc(),
        "finished_at": None,
        "error_class": None,
        "matrix_status": "FAIL",
        "failed_checks": [],
        "failed_checks_count": 0,
        "event_type": None,
        "alert_needed": False,
        "alert_sent": False,
        "alert_deduped": False,
        "bundle_dir": str(bundle_dir),
        "doctor_matrix_bundle_dir": "",
        "doctor_matrix_result_path": "",
        "state_path": str(state_root / "last_state.json"),
        "warnings": [],
    }

    try:
        alert_on_first_fail = parse_bool_env("BRAIN_LOOP_ALERT_ON_FIRST_FAIL", True)
        previous_state = read_json(state_root / "last_state.json")
        if previous_state is None and (state_root / "last_state.json").exists():
            result["warnings"].append("state_file_unreadable")

        mock_failed = [x.strip() for x in str(args.mock_failed_checks).split(",") if x.strip()]
        matrix_payload, matrix_warnings = run_matrix(
            mode=args.mode,
            mock=bool(args.mock),
            mock_status=str(args.mock_status),
            mock_failed_checks=mock_failed,
            artifacts_root=artifacts_root,
            brain_bundle_dir=bundle_dir,
        )
        if matrix_warnings:
            result["warnings"].extend(matrix_warnings)

        matrix_status = "PASS" if str(matrix_payload.get("status")) == "PASS" else "FAIL"
        failed_checks = normalize_failed_checks(matrix_payload.get("failed_checks") or [])
        doctor_bundle_dir = Path(str(matrix_payload.get("bundle_dir") or bundle_dir))
        doctor_result_path = doctor_bundle_dir / "RESULT.json"

        result["matrix_status"] = matrix_status
        result["failed_checks"] = failed_checks
        result["failed_checks_count"] = len(failed_checks)
        result["doctor_matrix_bundle_dir"] = str(doctor_bundle_dir)
        result["doctor_matrix_result_path"] = str(doctor_result_path)

        event_type, alert_needed = decide_event(
            prev_state=previous_state,
            matrix_status=matrix_status,
            failed_checks=failed_checks,
            alert_on_first_fail=alert_on_first_fail,
        )
        result["event_type"] = event_type
        result["alert_needed"] = alert_needed

        prior_hash = ""
        if isinstance(previous_state, dict):
            prior_hash = str(previous_state.get("last_alert_hash") or "")

        alert_hash = ""
        alert_sent = False
        alert_deduped = False

        if alert_needed and event_type:
            alert_hash = build_alert_hash(
                event_type=event_type,
                matrix_status=matrix_status,
                failed_checks=failed_checks,
            )
            if prior_hash and alert_hash == prior_hash:
                alert_deduped = True
            else:
                message = format_alert_message(
                    event_type=event_type,
                    matrix_status=matrix_status,
                    failed_checks=failed_checks,
                    brain_bundle_dir=bundle_dir,
                    doctor_bundle_dir=doctor_bundle_dir,
                )
                notify = send_discord_webhook_alert(content=message)
                alert_sent = bool(notify.get("ok"))
                if not alert_sent:
                    result["warnings"].append(str(notify.get("error_class") or "DISCORD_ALERT_FAILED"))

        result["alert_sent"] = alert_sent
        result["alert_deduped"] = alert_deduped

        last_alert_hash = prior_hash
        if alert_sent and alert_hash:
            last_alert_hash = alert_hash

        new_state = {
            "updated_at": now_utc(),
            "brain_run_id": run_id,
            "matrix_status": matrix_status,
            "failed_checks": failed_checks,
            "event_type": event_type or "NONE",
            "last_alert_hash": last_alert_hash,
        }
        try:
            atomic_write_json(state_root / "last_state.json", new_state)
        except OSError as exc:
            result["warnings"].append(f"STATE_WRITE_FAILED:{type(exc).__name__}")
            result["error_class"] = "STATE_WRITE_FAILED"

        doctor_ref = {
            "run_id": str(matrix_payload.get("run_id") or ""),
            "status": matrix_status,
            "failed_checks": failed_checks,
            "bundle_dir": str(doctor_bundle_dir),
            "result_path": str(doctor_result_path),
        }
        atomic_write_json(bundle_dir / "doctor_matrix_ref.json", doctor_ref)

        if result.get("error_class"):
            result["status"] = "FAIL"
            result["ok"] = False
        else:
            result["status"] = matrix_status
            result["ok"] = matrix_status == "PASS"

        result["finished_at"] = now_utc()
        atomic_write_json(bundle_dir / "RESULT.json", result)
        atomic_write_text(bundle_dir / "SUMMARY.md", build_summary(result))
        return (0 if result["status"] == "PASS" else 1), result
    except Exception as exc:  # noqa: BLE001
        trace = _bounded_traceback(traceback.format_exc())
        result["status"] = "FAIL"
        result["ok"] = False
        result["error_class"] = "BRAIN_LOOP_EXCEPTION"
        result["exception_type"] = type(exc).__name__
        result["finished_at"] = now_utc()
        tb_path = bundle_dir / "traceback.log"
        try:
            atomic_write_text(tb_path, trace)
            result["traceback_path"] = str(tb_path)
        except OSError:
            pass
        atomic_write_json(bundle_dir / "doctor_matrix_ref.json", {"status": "FAIL", "failed_checks": []})
        atomic_write_json(bundle_dir / "RESULT.json", result)
        atomic_write_text(bundle_dir / "SUMMARY.md", build_summary(result))
        return 1, result


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(argv or []))
    exit_code, payload = execute(args)
    print(json.dumps(payload, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

