#!/usr/bin/env python3
"""Soma Run to DONE — HQ orchestrator action.

Runs prechecks (drift→apply/deploy, hostd, noVNC), triggers soma_kajabi_auto_finish
via async exec, polls until RESULT.json, outputs SUCCESS with PROOF or WAITING_FOR_HUMAN.

Artifacts: artifacts/soma_kajabi/run_to_done/<run_id>/{PROOF.md, PROOF.json}
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

HQ_BASE = os.environ.get("OPENCLAW_HQ_BASE", "http://127.0.0.1:8787")
ADMIN_TOKEN = os.environ.get("OPENCLAW_ADMIN_TOKEN", "")
POLL_INTERVAL = 12
MAX_POLL_MINUTES = 35
NOVNC_FAST_TIMEOUT = 25
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


def _curl(method: str, path: str, data: dict | None = None, timeout: int = 30) -> tuple[int, str]:
    """Return (http_code, body)."""
    import urllib.request
    import urllib.error

    url = f"{HQ_BASE.rstrip('/')}{path}"
    headers = {"Content-Type": "application/json"}
    if ADMIN_TOKEN:
        headers["X-OpenClaw-Token"] = ADMIN_TOKEN
    req = urllib.request.Request(url, method=method, headers=headers)
    if data:
        req.data = json.dumps(data).encode("utf-8")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8") if e.fp else ""
    except Exception as e:
        return -1, str(e)


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
    code, _ = _curl("GET", "/api/exec?check=connectivity", timeout=10)
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
        code, _ = _curl("POST", "/api/exec", data={"action": "openclaw_novnc_restart"}, timeout=10)
        if code in (200, 202):
            time.sleep(15)
            return _run_doctor()
    return False


def main() -> int:
    root = _repo_root()
    run_id = f"run_to_done_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"
    out_dir = root / "artifacts" / "soma_kajabi" / "run_to_done" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    # PRECHECK
    if not _precheck_drift(root):
        (out_dir / "PRECHECK.json").write_text(
            json.dumps({"drift_deploy": "failed"}, indent=2)
        )
        print(json.dumps({"ok": False, "error_class": "DRIFT_DEPLOY_FAILED", "run_id": run_id}))
        return 1

    if not _precheck_hostd():
        (out_dir / "PRECHECK.json").write_text(
            json.dumps({"hostd": "unreachable"}, indent=2)
        )
        print(json.dumps({"ok": False, "error_class": "HOSTD_UNREACHABLE", "run_id": run_id}))
        return 1

    if not _precheck_novnc(root):
        (out_dir / "PRECHECK.json").write_text(
            json.dumps({"novnc": "not_ready"}, indent=2)
        )
        print(json.dumps({"ok": False, "error_class": "NOVNC_NOT_READY", "run_id": run_id}))
        return 1

    # TRIGGER
    code, body = _curl("POST", "/api/exec", data={"action": "soma_kajabi_auto_finish"}, timeout=5)
    if code not in (200, 202):
        try:
            data = json.loads(body)
            err = data.get("error_class", data.get("error", "unknown"))
        except Exception:
            err = body[:200]
        (out_dir / "TRIGGER.json").write_text(
            json.dumps({"http_code": code, "error": err}, indent=2)
        )
        print(json.dumps({"ok": False, "error_class": "TRIGGER_FAILED", "run_id": run_id, "message": err}))
        return 1

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        print(json.dumps({"ok": False, "error_class": "TRIGGER_PARSE_FAILED", "run_id": run_id}))
        return 1

    auto_run_id = data.get("run_id")
    if not auto_run_id:
        print(json.dumps({"ok": False, "error_class": "NO_RUN_ID", "run_id": run_id}))
        return 1

    # POLL
    start = time.monotonic()
    max_elapsed = MAX_POLL_MINUTES * 60
    artifact_dir: str | None = None
    result_data: dict | None = None

    while time.monotonic() - start < max_elapsed:
        code, body = _curl("GET", f"/api/runs?id={auto_run_id}", timeout=15)
        if code != 200:
            time.sleep(POLL_INTERVAL)
            continue

        try:
            resp = json.loads(body)
            run_obj = resp.get("run", {})
        except json.JSONDecodeError:
            time.sleep(POLL_INTERVAL)
            continue

        status = run_obj.get("status")
        artifact_dir = run_obj.get("artifact_dir")

        if artifact_dir:
            result_path = root / artifact_dir / "RESULT.json"
            if result_path.exists():
                result_data = json.loads(result_path.read_text())
                break

        if status and status not in ("running", "queued"):
            # Terminal from run record
            artifact_dir = run_obj.get("artifact_dir")
            if artifact_dir:
                result_path = root / artifact_dir / "RESULT.json"
                if result_path.exists():
                    result_data = json.loads(result_path.read_text())
            break

        time.sleep(POLL_INTERVAL)

    if not result_data:
        (out_dir / "POLL.json").write_text(
            json.dumps({"timeout": True, "auto_run_id": auto_run_id}, indent=2)
        )
        print(json.dumps({
            "ok": False,
            "error_class": "POLL_TIMEOUT",
            "run_id": run_id,
            "auto_run_id": auto_run_id,
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
        accept_dir = root / "artifacts" / "soma_kajabi" / "acceptance"
        # Find acceptance dir for this run (auto_finish run_id)
        hostd_run_id = Path(artifact_dir or "").name if artifact_dir else ""
        if hostd_run_id:
            accept_run_dir = accept_dir / hostd_run_id
        else:
            dirs = sorted([d for d in accept_dir.iterdir() if d.is_dir()], key=lambda d: d.name, reverse=True)
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

    # FAILURE or TIMEOUT
    error_class = result_data.get("error_class", "UNKNOWN")
    (out_dir / "PROOF.json").write_text(json.dumps({
        "run_id": run_id,
        "auto_run_id": auto_run_id,
        "status": terminal_status,
        "error_class": error_class,
    }, indent=2))
    print(json.dumps({
        "ok": False,
        "status": terminal_status,
        "error_class": error_class,
        "run_id": run_id,
        "artifact_dir": artifact_dir,
    }))
    return 1


if __name__ == "__main__":
    sys.exit(main())
