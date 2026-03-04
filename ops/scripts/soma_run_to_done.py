#!/usr/bin/env python3
"""Soma Run to DONE — HQ orchestrator action.

Runs prechecks (drift→apply/deploy, hostd, noVNC), triggers soma_kajabi_auto_finish
via async exec, polls until RESULT.json, outputs SUCCESS with PROOF or WAITING_FOR_HUMAN.

Artifacts: artifacts/soma_kajabi/run_to_done/<run_id>/{PROOF.md, PROOF.json}
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Shared trigger client — single source of truth for exec POST + status handling
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ops.lib.exec_trigger import hq_request, trigger_exec  # noqa: E402

HQ_BASE = os.environ.get("OPENCLAW_HQ_BASE", "http://127.0.0.1:8787")
POLL_INTERVAL_INIT = 6
POLL_INTERVAL_MAX = 24
MAX_POLL_MINUTES = 35
DEFAULT_MAX_POLLS = 120
NOVNC_DEEP_TIMEOUT = 180  # convergent DEEP doctor waits/retries up to 120s (hard cap 180s)
INSTRUCTION_LINE = (
    "Open the URL, complete Cloudflare/Kajabi login + 2FA, then go to Products → Courses "
    "and ensure Home User Library + Practitioner Library are visible; then stop touching the session."
)
LATEST_RUN_POINTER_NAME = "LATEST_RUN.json"


def _repo_root() -> Path:
    env = os.environ.get("OPENCLAW_REPO_ROOT")
    if env and Path(env).exists():
        return Path(env)
    cwd = Path.cwd()
    for _ in range(10):
        if (cwd / "config" / "project_state.json").exists():
            return cwd
        if cwd == cwd.parent:
            break
        cwd = cwd.parent
    return Path(env or "/opt/ai-ops-runner")


def _get_artifacts_root() -> Path:
    """Return canonical artifacts root for pointer files.

    Priority:
      1. OPENCLAW_ARTIFACTS_ROOT env var (if set and non-empty)
      2. /opt/ai-ops-runner/artifacts (if exists, canonical VPS path)
      3. <repo_root>/artifacts (local dev fallback)
    """
    env = os.environ.get("OPENCLAW_ARTIFACTS_ROOT", "").strip()
    if env:
        return Path(env)
    vps_path = Path("/opt/ai-ops-runner/artifacts")
    if vps_path.exists():
        return vps_path
    return _repo_root() / "artifacts"


def _get_build_sha(root: Path) -> str:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=str(root),
        )
        return (r.stdout or "").strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def _run(cmd: list[str], timeout: int = 600) -> tuple[int, str]:
    try:
        r = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            cwd=str(_repo_root()),
        )
        return r.returncode, r.stdout or ""
    except subprocess.TimeoutExpired:
        return -1, "timeout"
    except Exception as e:
        return -1, str(e)


def _precheck_drift(root: Path) -> bool:
    """If build_sha != origin/main, run deploy. Return True if OK to proceed."""
    build_sha = _get_build_sha(root)
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--short=12", "origin/main"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=str(root),
        )
        origin_sha = (r.stdout or "").strip() if r.returncode == 0 else ""
    except Exception:
        origin_sha = ""
    if not build_sha or not origin_sha:
        return True
    if build_sha == origin_sha:
        return True
    # Drift: run deploy
    deploy = root / "ops" / "deploy_pipeline.sh"
    if deploy.exists() and os.access(deploy, os.X_OK):
        rc, _ = _run([str(deploy)], timeout=600)
        return rc == 0
    return True


def _precheck_hostd() -> bool:
    code, _ = hq_request("GET", "/api/exec?check=connectivity", timeout=10)
    return code == 200


def _run_autorecover(root: Path) -> bool:
    """Invoke novnc_autorecover.py once. Return True if it fixed the issue."""
    autorecover = root / "ops" / "scripts" / "novnc_autorecover.py"
    if not autorecover.exists():
        return False
    try:
        r = subprocess.run(
            [sys.executable, str(autorecover)],
            capture_output=True,
            text=True,
            timeout=300,
            cwd=str(root),
        )
        return r.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def _precheck_novnc(
    root: Path,
    details: dict[str, str | None] | None = None,
) -> bool:
    doctor = root / "ops" / "openclaw_novnc_doctor.sh"
    if not doctor.exists() or not os.access(doctor, os.X_OK):
        if details is not None:
            details["error_class"] = "NOVNC_DOCTOR_MISSING"
            details["novnc_readiness_artifact_dir"] = None
        return False

    def _run_doctor() -> bool:
        try:
            r = subprocess.run(
                [str(doctor)],
                capture_output=True,
                text=True,
                timeout=NOVNC_DEEP_TIMEOUT,
                cwd=str(root),
            )
            line = (r.stdout or "").strip().split("\n")[-1]
            if not line:
                if details is not None:
                    details["error_class"] = "NOVNC_DOCTOR_NO_OUTPUT"
                return False
            doc = json.loads(line)
            if details is not None:
                details["error_class"] = str(doc.get("error_class") or "NOVNC_NOT_READY")
                details["novnc_readiness_artifact_dir"] = str(
                    doc.get("readiness_artifact_dir") or doc.get("artifact_dir") or ""
                ) or None
            return bool(r.returncode == 0 and doc.get("ok", False))
        except (subprocess.TimeoutExpired, json.JSONDecodeError):
            if details is not None:
                details["error_class"] = "NOVNC_DOCTOR_TIMEOUT"
            return False

    # openclaw_novnc_doctor is now convergent (probe+recover+backoff bounded loop).
    return _run_doctor()


def _write_json(path: Path, payload: dict) -> None:
    """Atomic-ish JSON write (write + flush)."""
    path.write_text(json.dumps(payload, indent=2))


def _utc_now_z() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalize_pointer_status(status: str) -> str:
    normalized = (status or "").strip().upper()
    if normalized in {"RUNNING", "WAITING_FOR_HUMAN", "SUCCESS", "FAIL"}:
        return normalized
    if normalized in {"FAILURE", "TIMEOUT", "ERROR", "ALREADY_RUNNING"}:
        return "FAIL"
    return "RUNNING"


def write_latest_run_pointer(
    out_dir: Path,
    run_id: str,
    status: str,
    error_class: str | None = None,
) -> None:
    """Atomically write LATEST_RUN.json to canonical artifacts root.

    Pointer is written to:
      ${ARTIFACTS_ROOT}/soma_kajabi/run_to_done/LATEST_RUN.json

    where ARTIFACTS_ROOT is determined by _get_artifacts_root() priority:
      1. OPENCLAW_ARTIFACTS_ROOT env var
      2. /opt/ai-ops-runner/artifacts (VPS canonical)
      3. <repo_root>/artifacts (local dev)
    """
    artifacts_root = _get_artifacts_root()
    pointer_dir = artifacts_root / "soma_kajabi" / "run_to_done"
    pointer_dir.mkdir(parents=True, exist_ok=True)
    pointer_path = pointer_dir / LATEST_RUN_POINTER_NAME
    payload = {
        "run_id": run_id,
        "run_dir": out_dir.name,
        "updated_at": _utc_now_z(),
        "status": _normalize_pointer_status(status),
        "error_class": error_class if isinstance(error_class, str) and error_class.strip() else None,
    }
    tmp_path = pointer_path.with_name(
        f".{LATEST_RUN_POINTER_NAME}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    tmp_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(pointer_path)


def _safe_write_latest_run_pointer(
    out_dir: Path,
    run_id: str,
    status: str,
    error_class: str | None = None,
) -> None:
    try:
        write_latest_run_pointer(out_dir, run_id, status=status, error_class=error_class)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "level": "WARN",
                    "event": "LATEST_RUN_POINTER_WRITE_FAILED",
                    "error": str(exc)[:200],
                }
            ),
            file=sys.stderr,
        )


def write_initial_proof_files(out_dir: Path, run_id: str) -> None:
    """Write PROOF.json and PRECHECK.json at run start so remote helpers never 404."""
    now = datetime.now(timezone.utc).isoformat()
    _write_json(out_dir / "PROOF.json", {
        "run_id": run_id,
        "status": "RUNNING",
        "phase": "init",
        "started_at": now,
        "project": "soma_kajabi",
        "action": "soma_run_to_done",
    })
    _write_json(out_dir / "PRECHECK.json", {
        "run_id": run_id,
        "status": "RUNNING",
        "precheck": "pending",
        "started_at": now,
    })


def _update_proof(out_dir: Path, run_id: str, updates: dict) -> None:
    """Merge updates into the existing PROOF.json."""
    proof_path = out_dir / "PROOF.json"
    try:
        current = json.loads(proof_path.read_text())
    except (OSError, json.JSONDecodeError):
        current = {"run_id": run_id}
    current.update(updates)
    _write_json(proof_path, current)


def _update_precheck(out_dir: Path, run_id: str, updates: dict) -> None:
    """Merge updates into the existing PRECHECK.json."""
    precheck_path = out_dir / "PRECHECK.json"
    try:
        current = json.loads(precheck_path.read_text())
    except (OSError, json.JSONDecodeError):
        current = {"run_id": run_id}
    current.update(updates)
    _write_json(precheck_path, current)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Soma Run to DONE orchestrator")
    p.add_argument("--max-polls", type=int, default=DEFAULT_MAX_POLLS)
    p.add_argument("--max-minutes", type=float, default=MAX_POLL_MINUTES)
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    max_polls = args.max_polls
    max_minutes = args.max_minutes

    root = _repo_root()
    run_id = f"run_to_done_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"
    pointer_run_id = (os.environ.get("OPENCLAW_RUN_ID") or run_id).strip() or run_id
    out_dir = root / "artifacts" / "soma_kajabi" / "run_to_done" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    # Write initial proof files immediately so remote helpers never 404
    write_initial_proof_files(out_dir, run_id)
    _safe_write_latest_run_pointer(out_dir, pointer_run_id, status="RUNNING")

    # PRECHECK
    _update_proof(out_dir, run_id, {"phase": "precheck"})

    if not _precheck_drift(root):
        _update_precheck(out_dir, run_id, {
            "status": "FAIL", "drift_deploy": "failed",
            "error_class": "DRIFT_DEPLOY_FAILED",
        })
        _update_proof(out_dir, run_id, {
            "status": "FAIL", "error_class": "DRIFT_DEPLOY_FAILED", "phase": "precheck",
        })
        _safe_write_latest_run_pointer(
            out_dir, pointer_run_id, status="FAIL", error_class="DRIFT_DEPLOY_FAILED"
        )
        print(json.dumps({"ok": False, "error_class": "DRIFT_DEPLOY_FAILED", "run_id": run_id, "project": "soma_kajabi", "action": "soma_run_to_done"}))
        return 1

    if not _precheck_hostd():
        _update_precheck(out_dir, run_id, {
            "status": "FAIL", "hostd": "unreachable",
            "error_class": "HOSTD_UNREACHABLE",
        })
        _update_proof(out_dir, run_id, {
            "status": "FAIL", "error_class": "HOSTD_UNREACHABLE", "phase": "precheck",
        })
        _safe_write_latest_run_pointer(
            out_dir, pointer_run_id, status="FAIL", error_class="HOSTD_UNREACHABLE"
        )
        print(json.dumps({"ok": False, "error_class": "HOSTD_UNREACHABLE", "run_id": run_id, "project": "soma_kajabi", "action": "soma_run_to_done"}))
        return 1

    novnc_precheck: dict[str, str | None] = {}
    if not _precheck_novnc(root, novnc_precheck):
        error_class = novnc_precheck.get("error_class") or "NOVNC_NOT_READY"
        novnc_artifact_dir = novnc_precheck.get("novnc_readiness_artifact_dir")
        _update_precheck(out_dir, run_id, {
            "status": "FAIL", "novnc": "not_ready",
            "error_class": error_class,
            "novnc_readiness_artifact_dir": novnc_artifact_dir,
        })
        _update_proof(out_dir, run_id, {
            "status": "FAIL", "error_class": error_class, "phase": "precheck",
            "novnc_readiness_artifact_dir": novnc_artifact_dir,
        })
        _safe_write_latest_run_pointer(
            out_dir, pointer_run_id, status="FAIL", error_class=error_class
        )
        print(
            json.dumps(
                {
                    "ok": False,
                    "error_class": error_class,
                    "run_id": run_id,
                    "project": "soma_kajabi",
                    "action": "soma_run_to_done",
                    "novnc_readiness_artifact_dir": novnc_artifact_dir,
                }
            )
        )
        return 1

    # Prechecks passed
    _update_precheck(out_dir, run_id, {"status": "PASS", "precheck": "passed"})

    # TRIGGER — uses shared exec trigger client (default 90s timeout, 409 = ALREADY_RUNNING)
    _update_proof(out_dir, run_id, {"phase": "trigger"})
    tr = trigger_exec("soma_kajabi", "soma_kajabi_auto_finish")

    if tr.state == "ALREADY_RUNNING":
        active_run_id = tr.run_id or "(unknown)"
        (out_dir / "TRIGGER.json").write_text(
            json.dumps({"http_code": 409, "state": "ALREADY_RUNNING", "active_run_id": active_run_id}, indent=2)
        )
        _update_proof(out_dir, run_id, {
            "status": "ALREADY_RUNNING", "phase": "trigger",
            "active_run_id": active_run_id,
        })
        _safe_write_latest_run_pointer(
            out_dir, pointer_run_id, status="FAIL", error_class="ALREADY_RUNNING"
        )
        print(json.dumps({
            "ok": False,
            "error_class": "ALREADY_RUNNING",
            "run_id": run_id,
            "active_run_id": active_run_id,
            "message": f"Run already in progress for project=soma_kajabi. Not starting a second run.",
            "project": "soma_kajabi",
            "action": "soma_run_to_done",
        }))
        return 0

    if tr.state == "FAILED":
        (out_dir / "TRIGGER.json").write_text(
            json.dumps({"http_code": tr.status_code, "error": tr.message}, indent=2)
        )
        _update_proof(out_dir, run_id, {
            "status": "FAIL", "error_class": "TRIGGER_FAILED", "phase": "trigger",
        })
        _safe_write_latest_run_pointer(
            out_dir, pointer_run_id, status="FAIL", error_class="TRIGGER_FAILED"
        )
        print(json.dumps({
            "ok": False,
            "error_class": "TRIGGER_FAILED",
            "run_id": run_id,
            "message": tr.message,
            "project": "soma_kajabi",
            "action": "soma_run_to_done",
        }))
        return 1

    # ACCEPTED
    auto_run_id = tr.run_id
    if not auto_run_id:
        _update_proof(out_dir, run_id, {
            "status": "FAIL", "error_class": "NO_RUN_ID", "phase": "trigger",
        })
        _safe_write_latest_run_pointer(
            out_dir, pointer_run_id, status="FAIL", error_class="NO_RUN_ID"
        )
        print(json.dumps({"ok": False, "error_class": "NO_RUN_ID", "run_id": run_id}))
        return 1

    _update_proof(out_dir, run_id, {
        "phase": "polling", "auto_run_id": auto_run_id,
    })

    # POLL — exponential backoff governor
    start = time.monotonic()
    max_elapsed = max_minutes * 60
    artifact_dir: str | None = None
    result_data: dict | None = None
    poll_interval = POLL_INTERVAL_INIT
    poll_count = 0
    prev_status: str | None = None

    while time.monotonic() - start < max_elapsed and poll_count < max_polls:
        poll_count += 1
        code, body = hq_request("GET", f"/api/runs?id={auto_run_id}", timeout=15)
        if code != 200:
            time.sleep(poll_interval)
            poll_interval = min(poll_interval * 2, POLL_INTERVAL_MAX)
            continue

        try:
            resp = json.loads(body)
            run_obj = resp.get("run", {})
        except json.JSONDecodeError:
            time.sleep(poll_interval)
            poll_interval = min(poll_interval * 2, POLL_INTERVAL_MAX)
            continue

        status = run_obj.get("status")
        artifact_dir = run_obj.get("artifact_dir")

        # Reset backoff on state change
        if status != prev_status:
            poll_interval = POLL_INTERVAL_INIT
            prev_status = status

        if artifact_dir:
            result_path = root / artifact_dir / "RESULT.json"
            if result_path.exists():
                result_data = json.loads(result_path.read_text())
                break

        # Early stop: acceptance proof already available via status endpoint
        if artifact_dir:
            accept_base = root / "artifacts" / "soma_kajabi" / "acceptance"
            if accept_base.exists():
                summary_path = root / artifact_dir / "SUMMARY.json"
                if summary_path.exists():
                    try:
                        sf = json.loads(summary_path.read_text())
                        ap = (sf.get("artifact_dirs") or {}).get("acceptance", "")
                        if ap:
                            mr = root / ap / "mirror_report.json"
                            if mr.exists():
                                mr_data = json.loads(mr.read_text())
                                if len(mr_data.get("exceptions", [])) == 0:
                                    result_data = {"status": "SUCCESS", "early_stop": "acceptance_proof_available"}
                                    break
                    except (json.JSONDecodeError, OSError):
                        pass

        if status and status not in ("running", "queued"):
            artifact_dir = run_obj.get("artifact_dir")
            if artifact_dir:
                result_path = root / artifact_dir / "RESULT.json"
                if result_path.exists():
                    result_data = json.loads(result_path.read_text())
            break

        time.sleep(poll_interval)
        poll_interval = min(poll_interval * 2, POLL_INTERVAL_MAX)

    elapsed_sec = round(time.monotonic() - start, 1)

    # Record poll metrics
    poll_metrics = {
        "poll_count": poll_count,
        "elapsed_sec": elapsed_sec,
        "max_polls": max_polls,
        "max_minutes": max_minutes,
        "final_interval": poll_interval,
    }
    (out_dir / "poll_metrics.json").write_text(json.dumps(poll_metrics, indent=2))

    if not result_data:
        (out_dir / "POLL.json").write_text(
            json.dumps({"timeout": True, "auto_run_id": auto_run_id, "run_id": run_id}, indent=2)
        )
        _update_proof(out_dir, run_id, {
            "status": "FAIL", "error_class": "POLL_TIMEOUT", "phase": "polling",
            "auto_run_id": auto_run_id,
            "elapsed_sec": elapsed_sec,
        })
        _safe_write_latest_run_pointer(
            out_dir, pointer_run_id, status="FAIL", error_class="POLL_TIMEOUT"
        )
        print(json.dumps({
            "ok": False,
            "error_class": "POLL_TIMEOUT",
            "run_id": run_id,
            "auto_run_id": auto_run_id,
            "project": "soma_kajabi",
            "action": "soma_run_to_done",
        }))
        return 1

    terminal_status = result_data.get("status", "UNKNOWN")

    if terminal_status == "WAITING_FOR_HUMAN":
        novnc_url = result_data.get("novnc_url", "")
        instruction = result_data.get("instruction_line", INSTRUCTION_LINE)
        if novnc_url:
            try:
                sys.path.insert(0, str(root))
                from ops.lib.human_gate import write_gate, write_gate_artifact
                gate = write_gate("soma_kajabi", auto_run_id or run_id, novnc_url, "waiting_for_human_run_to_done")
                write_gate_artifact("soma_kajabi", auto_run_id or run_id, gate)
            except Exception:
                pass
        _update_proof(out_dir, run_id, {
            "auto_run_id": auto_run_id,
            "status": "WAITING_FOR_HUMAN",
            "phase": "human_gate",
            "novnc_url": novnc_url,
            "instruction_line": instruction,
            "artifact_dir": artifact_dir,
            "build_sha": _get_build_sha(root),
        })
        _safe_write_latest_run_pointer(out_dir, pointer_run_id, status="WAITING_FOR_HUMAN")
        (out_dir / "PROOF.md").write_text(
            f"# Soma Run to DONE — WAITING_FOR_HUMAN\n\n"
            f"**novnc_url**: {novnc_url}\n\n"
            f"**Instruction**: {instruction}\n"
        )
        print(json.dumps({
            "ok": False,
            "status": "WAITING_FOR_HUMAN",
            "run_id": run_id,
            "novnc_url": novnc_url,
            "instruction_line": instruction,
            "artifact_dir": artifact_dir or f"artifacts/soma_kajabi/run_to_done/{run_id}",
        }))
        return 0

    _update_proof(out_dir, run_id, {"phase": "acceptance_verification"})

    if terminal_status == "SUCCESS":
        accept_base = root / "artifacts" / "soma_kajabi" / "acceptance"
        accept_run_dir = None

        if artifact_dir:
            summary_path = root / artifact_dir / "SUMMARY.json"
            if summary_path.exists():
                try:
                    af_summary = json.loads(summary_path.read_text())
                    rel_path = (af_summary.get("artifact_dirs") or {}).get("acceptance", "")
                    if rel_path:
                        candidate = root / rel_path
                        if candidate.exists():
                            accept_run_dir = candidate
                except (json.JSONDecodeError, OSError):
                    pass

        if not accept_run_dir:
            hostd_run_id = Path(artifact_dir or "").name if artifact_dir else ""
            if hostd_run_id:
                candidate = accept_base / hostd_run_id
                if candidate.exists():
                    accept_run_dir = candidate

        # Fail-closed: no "latest" fallback — acceptance must be from this run
        if not accept_run_dir:
            _update_proof(out_dir, run_id, {
                "auto_run_id": auto_run_id,
                "status": "FAILURE",
                "error_class": "ACCEPTANCE_MISSING_FOR_RUN",
                "build_sha": _get_build_sha(root),
                "expected_paths": [
                    str(accept_base / (Path(artifact_dir or "").name or "UNKNOWN")),
                ],
            })
            _safe_write_latest_run_pointer(
                out_dir, pointer_run_id, status="FAIL", error_class="ACCEPTANCE_MISSING_FOR_RUN"
            )
            print(json.dumps({
                "ok": False,
                "status": "FAILURE",
                "error_class": "ACCEPTANCE_MISSING_FOR_RUN",
                "run_id": run_id,
                "auto_run_id": auto_run_id,
                "project": "soma_kajabi",
                "action": "soma_run_to_done",
            }))
            return 1

        mirror_pass = False
        exceptions_count = -1
        acceptance_rel = str(accept_run_dir.relative_to(root))

        if (accept_run_dir / "mirror_report.json").exists():
            mr = json.loads((accept_run_dir / "mirror_report.json").read_text())
            excs = mr.get("exceptions", [])
            exceptions_count = len(excs)
            mirror_pass = exceptions_count == 0
        else:
            # mirror_report.json missing → fail-closed
            _update_proof(out_dir, run_id, {
                "auto_run_id": auto_run_id,
                "status": "FAILURE",
                "error_class": "ACCEPTANCE_MISSING_FOR_RUN",
                "build_sha": _get_build_sha(root),
                "acceptance_dir": acceptance_rel,
                "message": "mirror_report.json not found in acceptance dir",
            })
            _safe_write_latest_run_pointer(
                out_dir, pointer_run_id, status="FAIL", error_class="ACCEPTANCE_MISSING_FOR_RUN"
            )
            print(json.dumps({
                "ok": False,
                "status": "FAILURE",
                "error_class": "ACCEPTANCE_MISSING_FOR_RUN",
                "run_id": run_id,
                "auto_run_id": auto_run_id,
                "acceptance_dir": acceptance_rel,
                "project": "soma_kajabi",
                "action": "soma_run_to_done",
            }))
            return 1

        # Fail-closed: mirror must PASS for SUCCESS
        if not mirror_pass:
            _update_proof(out_dir, run_id, {
                "auto_run_id": auto_run_id,
                "status": "FAILURE",
                "error_class": "MIRROR_FAIL",
                "build_sha": _get_build_sha(root),
                "acceptance_dir": acceptance_rel,
                "mirror_pass": False,
                "mirror_exceptions_count": exceptions_count,
            })
            _safe_write_latest_run_pointer(
                out_dir, pointer_run_id, status="FAIL", error_class="MIRROR_FAIL"
            )
            (out_dir / "PROOF.md").write_text(
                f"# Soma Run to DONE — FAILURE (Mirror)\n\n"
                f"- build_sha: {_get_build_sha(root)}\n"
                f"- acceptance: {acceptance_rel}\n"
                f"- Mirror PASS: False (exceptions_count={exceptions_count})\n"
                f"- error_class: MIRROR_FAIL\n"
            )
            print(json.dumps({
                "ok": False,
                "status": "FAILURE",
                "error_class": "MIRROR_FAIL",
                "run_id": run_id,
                "auto_run_id": auto_run_id,
                "acceptance_dir": acceptance_rel,
                "mirror_pass": False,
                "mirror_exceptions_count": exceptions_count,
                "project": "soma_kajabi",
                "action": "soma_run_to_done",
            }))
            return 1

        _update_proof(out_dir, run_id, {
            "auto_run_id": auto_run_id,
            "status": "SUCCESS",
            "phase": "done",
            "build_sha": _get_build_sha(root),
            "acceptance_path": acceptance_rel,
            "acceptance_dir": acceptance_rel,
            "mirror_pass": True,
            "mirror_exceptions_count": 0,
            "exceptions_count": 0,
        })
        _safe_write_latest_run_pointer(out_dir, pointer_run_id, status="SUCCESS")
        (out_dir / "PROOF.md").write_text(
            f"# Soma Run to DONE — SUCCESS\n\n"
            f"- build_sha: {_get_build_sha(root)}\n"
            f"- acceptance: {acceptance_rel}\n"
            f"- Mirror PASS: True (exceptions_count=0)\n"
        )
        print(json.dumps({
            "ok": True,
            "status": "SUCCESS",
            "run_id": run_id,
            "auto_run_id": auto_run_id,
            "acceptance_path": acceptance_rel,
            "acceptance_dir": acceptance_rel,
            "mirror_pass": True,
            "mirror_exceptions_count": 0,
            "proof_artifact": f"artifacts/soma_kajabi/run_to_done/{run_id}/PROOF.json",
        }))
        return 0

    # FAILURE or TIMEOUT — reclassify auth gates as WAITING_FOR_HUMAN (never false-fail)
    error_class = result_data.get("error_class", "UNKNOWN")
    AUTH_NEEDED = frozenset({
        "KAJABI_CLOUDFLARE_BLOCKED", "KAJABI_NOT_LOGGED_IN", "KAJABI_SESSION_EXPIRED",
        "KAJABI_CAPTURE_INTERACTIVE_FAILED", "SESSION_CHECK_TIMEOUT", "SESSION_CHECK_BROWSER_CLOSED",
        "KAJABI_INTERACTIVE_CAPTURE_ERROR", "KAJABI_INTERACTIVE_CAPTURE_TIMEOUT",
    })
    if terminal_status in ("FAILURE", "TIMEOUT") and error_class in AUTH_NEEDED:
        novnc_url = result_data.get("novnc_url", "")
        instruction = result_data.get("instruction_line", INSTRUCTION_LINE)
        if not novnc_url and artifact_dir:
            wfh_path = root / artifact_dir / "WAITING_FOR_HUMAN.json"
            if wfh_path.exists():
                try:
                    wfh = json.loads(wfh_path.read_text())
                    novnc_url = wfh.get("novnc_url", "")
                    instruction = wfh.get("instruction_line", instruction)
                except (json.JSONDecodeError, OSError):
                    pass
        if not novnc_url and (root / "ops" / "openclaw_novnc_doctor.sh").exists():
            try:
                r = subprocess.run(
                    [str(root / "ops" / "openclaw_novnc_doctor.sh")],
                    capture_output=True, text=True, timeout=60, cwd=str(root),
                )
                if r.returncode == 0:
                    line = (r.stdout or "").strip().split("\n")[-1]
                    if line:
                        doc = json.loads(line)
                        novnc_url = doc.get("novnc_url", "")
            except (subprocess.TimeoutExpired, json.JSONDecodeError):
                pass
        if novnc_url:
            try:
                from ops.lib.human_gate import write_gate, write_gate_artifact
                gate = write_gate("soma_kajabi", auto_run_id or run_id, novnc_url, "auth_gate_reclassified")
                write_gate_artifact("soma_kajabi", auto_run_id or run_id, gate)
            except Exception:
                pass
        _update_proof(out_dir, run_id, {
            "auto_run_id": auto_run_id,
            "status": "WAITING_FOR_HUMAN",
            "phase": "human_gate",
            "novnc_url": novnc_url,
            "instruction_line": instruction,
            "artifact_dir": artifact_dir,
            "build_sha": _get_build_sha(root),
        })
        _safe_write_latest_run_pointer(out_dir, pointer_run_id, status="WAITING_FOR_HUMAN")
        (out_dir / "PROOF.md").write_text(
            f"# Soma Run to DONE — WAITING_FOR_HUMAN (auth gate)\n\n"
            f"**novnc_url**: {novnc_url}\n\n**Instruction**: {instruction}\n"
        )
        print(json.dumps({
            "ok": False,
            "status": "WAITING_FOR_HUMAN",
            "run_id": run_id,
            "novnc_url": novnc_url,
            "instruction_line": instruction,
            "artifact_dir": artifact_dir or f"artifacts/soma_kajabi/run_to_done/{run_id}",
        }))
        return 0
    _update_proof(out_dir, run_id, {
        "auto_run_id": auto_run_id,
        "status": terminal_status,
        "phase": "done",
        "error_class": error_class,
        "project": "soma_kajabi",
        "action": "soma_run_to_done",
    })
    _safe_write_latest_run_pointer(
        out_dir,
        pointer_run_id,
        status=terminal_status,
        error_class=error_class,
    )
    print(json.dumps({
        "ok": False,
        "status": terminal_status,
        "error_class": error_class,
        "run_id": run_id,
        "auto_run_id": auto_run_id,
        "project": "soma_kajabi",
        "action": "soma_run_to_done",
        "artifact_dir": artifact_dir,
    }))
    return 1


if __name__ == "__main__":
    sys.exit(main())
