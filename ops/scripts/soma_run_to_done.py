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
NOVNC_FAST_TIMEOUT = 90  # DEEP doctor: framebuffer warm-up (6×5s) + WS stability
INSTRUCTION_LINE = (
    "Open the URL, complete Cloudflare/Kajabi login + 2FA, then go to Products → Courses "
    "and ensure Home User Library + Practitioner Library are visible; then stop touching the session."
)


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


def _precheck_novnc(root: Path, retry_after_restart: bool = True) -> bool:
    doctor = root / "ops" / "openclaw_novnc_doctor.sh"
    if not doctor.exists() or not os.access(doctor, os.X_OK):
        return True

    def _run_doctor() -> bool:
        try:
            r = subprocess.run(
                [str(doctor)],
                capture_output=True,
                text=True,
                timeout=NOVNC_FAST_TIMEOUT,
                cwd=str(root),
            )
            if r.returncode != 0:
                return False
            line = (r.stdout or "").strip().split("\n")[-1]
            if line:
                doc = json.loads(line)
                return doc.get("ok", False)
            return False
        except (subprocess.TimeoutExpired, json.JSONDecodeError):
            return False

    if _run_doctor():
        return True
    if retry_after_restart:
        tr = trigger_exec("system", "openclaw_novnc_restart", timeout=10)
        if tr.state == "ACCEPTED":
            time.sleep(15)
            return _run_doctor()
    return False


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
    out_dir = root / "artifacts" / "soma_kajabi" / "run_to_done" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    # PRECHECK
    if not _precheck_drift(root):
        (out_dir / "PRECHECK.json").write_text(
            json.dumps({"drift_deploy": "failed", "run_id": run_id}, indent=2)
        )
        print(json.dumps({"ok": False, "error_class": "DRIFT_DEPLOY_FAILED", "run_id": run_id, "project": "soma_kajabi", "action": "soma_run_to_done"}))
        return 1

    if not _precheck_hostd():
        (out_dir / "PRECHECK.json").write_text(
            json.dumps({"hostd": "unreachable", "run_id": run_id}, indent=2)
        )
        print(json.dumps({"ok": False, "error_class": "HOSTD_UNREACHABLE", "run_id": run_id, "project": "soma_kajabi", "action": "soma_run_to_done"}))
        return 1

    if not _precheck_novnc(root):
        (out_dir / "PRECHECK.json").write_text(
            json.dumps({"novnc": "not_ready", "run_id": run_id}, indent=2)
        )
        print(json.dumps({"ok": False, "error_class": "NOVNC_NOT_READY", "run_id": run_id, "project": "soma_kajabi", "action": "soma_run_to_done"}))
        return 1

    # TRIGGER — uses shared exec trigger client (default 90s timeout, 409 = ALREADY_RUNNING)
    tr = trigger_exec("soma_kajabi", "soma_kajabi_auto_finish")

    if tr.state == "ALREADY_RUNNING":
        active_run_id = tr.run_id or "(unknown)"
        (out_dir / "TRIGGER.json").write_text(
            json.dumps({"http_code": 409, "state": "ALREADY_RUNNING", "active_run_id": active_run_id}, indent=2)
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
        print(json.dumps({"ok": False, "error_class": "NO_RUN_ID", "run_id": run_id}))
        return 1

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
        proof = {
            "run_id": run_id,
            "auto_run_id": auto_run_id,
            "status": "WAITING_FOR_HUMAN",
            "novnc_url": novnc_url,
            "instruction_line": instruction,
            "artifact_dir": artifact_dir,
            "build_sha": _get_build_sha(root),
        }
        (out_dir / "PROOF.json").write_text(json.dumps(proof, indent=2))
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

        if not accept_run_dir and accept_base.exists():
            dirs = sorted(
                [d for d in accept_base.iterdir() if d.is_dir()],
                key=lambda d: d.name,
                reverse=True,
            )
            accept_run_dir = dirs[0] if dirs else None

        mirror_pass = False
        exceptions_count = -1
        if accept_run_dir and (accept_run_dir / "mirror_report.json").exists():
            mr = json.loads((accept_run_dir / "mirror_report.json").read_text())
            excs = mr.get("exceptions", [])
            exceptions_count = len(excs)
            mirror_pass = exceptions_count == 0

        proof = {
            "run_id": run_id,
            "auto_run_id": auto_run_id,
            "status": "SUCCESS",
            "build_sha": _get_build_sha(root),
            "acceptance_path": str(accept_run_dir.relative_to(root)) if accept_run_dir else None,
            "mirror_pass": mirror_pass,
            "exceptions_count": exceptions_count,
        }
        (out_dir / "PROOF.json").write_text(json.dumps(proof, indent=2))
        (out_dir / "PROOF.md").write_text(
            f"# Soma Run to DONE — SUCCESS\n\n"
            f"- build_sha: {proof['build_sha']}\n"
            f"- acceptance: {proof['acceptance_path']}\n"
            f"- Mirror PASS: {mirror_pass} (exceptions_count={exceptions_count})\n"
        )
        print(json.dumps({
            "ok": True,
            "status": "SUCCESS",
            "run_id": run_id,
            "auto_run_id": auto_run_id,
            "acceptance_path": proof["acceptance_path"],
            "mirror_pass": mirror_pass,
            "exceptions_count": exceptions_count,
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
        proof = {
            "run_id": run_id,
            "auto_run_id": auto_run_id,
            "status": "WAITING_FOR_HUMAN",
            "novnc_url": novnc_url,
            "instruction_line": instruction,
            "artifact_dir": artifact_dir,
            "build_sha": _get_build_sha(root),
        }
        (out_dir / "PROOF.json").write_text(json.dumps(proof, indent=2))
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
    (out_dir / "PROOF.json").write_text(json.dumps({
        "run_id": run_id,
        "auto_run_id": auto_run_id,
        "status": terminal_status,
        "error_class": error_class,
        "project": "soma_kajabi",
        "action": "soma_run_to_done",
    }, indent=2))
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
