#!/usr/bin/env python3
"""Soma Kajabi Auto-Finish — connectors_status → Phase0 → Finish Plan with auto-validation.

Runs Phase0→FinishPlan automatically. Handles Cloudflare (spawns capture_interactive,
emits WAITING_FOR_HUMAN with noVNC URL, waits for completion, retries Phase0).
Uses exit node wrapper when /etc/ai-ops-runner/config/soma_kajabi_exit_node.txt exists.
Produces one canonical summary artifact at artifacts/soma_kajabi/auto_finish/<run_id>/.
Writes acceptance artifacts under artifacts/soma_kajabi/acceptance/<run_id>/.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Required offer URLs per SOMA_LOCKED_SPEC (fail-closed if not found when checkable)
REQUIRED_OFFER_URLS = ["/offers/q6ntyjef/checkout", "/offers/MHMmHyVZ/checkout"]

KAJABI_CLOUDFLARE_BLOCKED = "KAJABI_CLOUDFLARE_BLOCKED"
EXIT_NODE_OFFLINE = "EXIT_NODE_OFFLINE"
EXIT_NODE_ENABLE_FAILED = "EXIT_NODE_ENABLE_FAILED"
HOSTD_UNREACHABLE = "HOSTD_UNREACHABLE"
STORAGE_STATE_PATH = Path("/etc/ai-ops-runner/secrets/soma_kajabi/kajabi_storage_state.json")
EXIT_NODE_CONFIG = Path("/etc/ai-ops-runner/config/soma_kajabi_exit_node.txt")
CAPTURE_TIMEOUT = 1320  # 22 min
PHASE0_TIMEOUT = 320
FINISH_PLAN_TIMEOUT = 70
CAPTURE_POLL_TIMEOUT = 30 * 60  # 30 min for human to complete


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


def _run(cmd: list[str], timeout: int = 600, stream_stderr: bool = False) -> tuple[int, str]:
    """Run command, return (exit_code, stdout)."""
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=sys.stderr if stream_stderr else subprocess.PIPE,
            text=True,
            timeout=timeout,
            cwd=str(_repo_root()),
        )
        return result.returncode, result.stdout or ""
    except subprocess.TimeoutExpired:
        return -1, "timeout"
    except Exception as e:
        return -1, str(e)


def _run_with_exit_node(cmd: list[str], timeout: int) -> tuple[int, str]:
    """Run command via with_exit_node.sh if config exists."""
    root = _repo_root()
    if not EXIT_NODE_CONFIG.exists() or EXIT_NODE_CONFIG.read_text().strip() == "":
        return _run(cmd, timeout=timeout)

    wrapper = root / "ops" / "with_exit_node.sh"
    if not wrapper.exists():
        return _run(cmd, timeout=timeout)

    full_cmd = [str(wrapper), "--", *cmd]
    rc, out = _run(full_cmd, timeout=timeout)
    if rc != 0 and ("EXIT_NODE_OFFLINE" in out or "EXIT_NODE_ENABLE_FAILED" in out):
        try:
            last = out.strip().split("\n")[-1] if out else "{}"
            doc = json.loads(last) if last.startswith("{") else {}
            err = doc.get("error_class", "EXIT_NODE_OFFLINE")
            return rc, json.dumps({"ok": False, "error_class": err, "message": doc.get("message", out)})
        except json.JSONDecodeError:
            pass
    return rc, out


def _parse_last_json_line(text: str) -> dict:
    """Parse last line as JSON if it looks like JSON."""
    if not text:
        return {}
    lines = text.strip().split("\n")
    for line in reversed(lines):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                pass
    return {}


def _check_offer_urls(root: Path) -> tuple[str, bool]:
    """Check required offer URLs. Returns (status, pass).
    status: 'ok' | 'REQUIRES_HUMAN_CONFIRMATION' | 'FAIL:<reason>'
    """
    discover_base = root / "artifacts" / "soma_kajabi" / "discover"
    if not discover_base.exists():
        return "REQUIRES_HUMAN_CONFIRMATION", True  # Can't check, don't fail
    dirs = sorted([d for d in discover_base.iterdir() if d.is_dir()], key=lambda d: d.name, reverse=True)
    for d in dirs[:3]:
        page_html = d / "page.html"
        products_json = d / "products.json"
        if page_html.exists():
            content = page_html.read_text(errors="replace")
            found = [u for u in REQUIRED_OFFER_URLS if u in content]
            if len(found) < len(REQUIRED_OFFER_URLS):
                missing = [u for u in REQUIRED_OFFER_URLS if u not in content]
                return f"FAIL: Offer URLs not found on page: {missing}", False
            return "ok", True
    return "REQUIRES_HUMAN_CONFIRMATION", True


def _update_project_state_fail(root: Path, run_id: str) -> None:
    """Update project_state with FAIL status."""
    state_path = root / "config" / "project_state.json"
    if not state_path.exists():
        return
    try:
        state = json.loads(state_path.read_text())
        projects = state.setdefault("projects", {})
        sk = projects.setdefault("soma_kajabi", {})
        sk["last_auto_finish_status"] = "FAIL"
        sk["last_auto_finish_run_id"] = run_id
        sk["last_auto_finish_artifact_dir"] = f"artifacts/soma_kajabi/auto_finish/{run_id}"
        state_path.write_text(json.dumps(state, indent=2))
    except (OSError, json.JSONDecodeError):
        pass


def _fail_closed(out_dir: Path, run_id: str, error_class: str, message: str) -> int:
    """Write minimal summary and exit 1."""
    summary = {
        "ok": False,
        "run_id": run_id,
        "error_class": error_class,
        "message": message,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }
    (out_dir / "SUMMARY.json").write_text(json.dumps(summary, indent=2))
    (out_dir / "SUMMARY.md").write_text(
        f"# Auto-Finish Soma — FAIL\n\n**{error_class}**: {message}\n"
    )
    _update_project_state_fail(_repo_root(), run_id)
    print(json.dumps(summary))
    return 1


def main() -> int:
    root = _repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    run_id = f"auto_finish_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"
    out_dir = root / "artifacts" / "soma_kajabi" / "auto_finish" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    venv_python = root / ".venv-hostd" / "bin" / "python"
    if not venv_python.exists():
        venv_python = Path(sys.executable)

    # ── A) Preflight ──
    if not STORAGE_STATE_PATH.exists() or STORAGE_STATE_PATH.stat().st_size == 0:
        return _fail_closed(
            out_dir, run_id, "KAJABI_STORAGE_STATE_MISSING",
            "Kajabi connector not configured. Run Kajabi Bootstrap first."
        )

    use_exit_node = EXIT_NODE_CONFIG.exists() and EXIT_NODE_CONFIG.read_text().strip() != ""

    # ── B) connectors_status ──
    rc, conn_out = _run(
        [str(venv_python), "-m", "services.soma_kajabi.connectors_status"],
        timeout=20,
    )
    connectors_result: dict = {}
    if rc == 0:
        try:
            connectors_result = json.loads(conn_out)
        except json.JSONDecodeError:
            connectors_result = {"raw": conn_out[:500]}

    # ── C) Phase0 (with optional exit node, Cloudflare handling) ──
    phase0_cmd = [str(venv_python), "-m", "services.soma_kajabi.phase0_runner"]
    phase0_run_id: str | None = None
    capture_run_id: str | None = None
    max_capture_attempts = 1

    for capture_attempt in range(max_capture_attempts + 1):
        if use_exit_node:
            rc, phase0_out = _run_with_exit_node(phase0_cmd, timeout=PHASE0_TIMEOUT)
        else:
            rc, phase0_out = _run(phase0_cmd, timeout=PHASE0_TIMEOUT)

        doc = _parse_last_json_line(phase0_out)
        phase0_run_id = doc.get("run_id") or phase0_run_id
        error_class = doc.get("error_class")

        if rc == 0 and doc.get("ok"):
            break

        if error_class == KAJABI_CLOUDFLARE_BLOCKED and capture_attempt < max_capture_attempts:
            cap_script = root / "ops" / "scripts" / "kajabi_capture_interactive.py"
            if not cap_script.exists():
                return _fail_closed(
                    out_dir, run_id, "KAJABI_CAPTURE_SCRIPT_MISSING",
                    "kajabi_capture_interactive.py not found"
                )

            # Ensure noVNC ready before WAITING_FOR_HUMAN (restart + poll probe)
            sys.path.insert(0, str(root / "ops" / "scripts"))
            from novnc_ready import ensure_novnc_ready
            ready, url, err_class, journal_artifact = ensure_novnc_ready(out_dir, run_id)
            if not ready and err_class:
                return _fail_closed(
                    out_dir, run_id, err_class,
                    f"noVNC backend unavailable. Journal: {journal_artifact or 'N/A'}"
                )

            print("\n--- WAITING_FOR_HUMAN ---")
            print("noVNC READY")
            print(url)
            print("1. Open the URL in your browser (Tailscale network).")
            print("2. Complete the Cloudflare challenge and log in.")
            print("The run will auto-resume after completion.")
            sys.stdout.flush()
            cap_rc, cap_out = _run(
                [str(venv_python), str(cap_script)],
                timeout=CAPTURE_TIMEOUT,
                stream_stderr=True,
            )
            cap_doc = _parse_last_json_line(cap_out)
            capture_run_id = cap_doc.get("run_id") or cap_doc.get("artifact_dir", "").split("/")[-1]

            if cap_rc != 0:
                return _fail_closed(
                    out_dir, run_id, "KAJABI_CAPTURE_INTERACTIVE_FAILED",
                    cap_doc.get("message", cap_out[:300]) or "Capture failed"
                )
            continue

        return _fail_closed(
            out_dir, run_id, error_class or "PHASE0_FAILED",
            doc.get("recommended_next_action", phase0_out[:500]) or "Phase0 failed"
        )

    # ── D) Zane Finish Plan ──
    rc, finish_out = _run(
        [str(venv_python), "-m", "services.soma_kajabi.zane_finish_plan"],
        timeout=FINISH_PLAN_TIMEOUT,
    )
    finish_doc = _parse_last_json_line(finish_out)
    finish_run_id = finish_doc.get("run_id")
    if rc != 0:
        return _fail_closed(
            out_dir, run_id, "FINISH_PLAN_FAILED",
            finish_doc.get("error", finish_out[:300]) or "Zane Finish Plan failed"
        )

    # ── E) Validation gates ──
    phase0_root = root / "artifacts" / "soma_kajabi" / "phase0"
    phase0_dir = None
    if phase0_run_id:
        phase0_dir = phase0_root / phase0_run_id
    if not phase0_dir or not phase0_dir.exists():
        dirs = sorted([d for d in phase0_root.iterdir() if d.is_dir()], key=lambda d: d.name, reverse=True)
        phase0_dir = dirs[0] if dirs else None

    if not phase0_dir or not (phase0_dir / "kajabi_library_snapshot.json").exists():
        return _fail_closed(out_dir, run_id, "PHASE0_ARTIFACTS_MISSING", "Phase0 artifacts not found")

    snap_path = phase0_dir / "kajabi_library_snapshot.json"
    snap = json.loads(snap_path.read_text()) if snap_path.exists() else {}
    home_modules = len(snap.get("home", {}).get("modules", []))
    home_lessons = len(snap.get("home", {}).get("lessons", []))
    pract_lessons = len(snap.get("practitioner", {}).get("lessons", []))

    finish_root = root / "artifacts" / "soma_kajabi" / "zane_finish_plan"
    finish_dir = finish_root / finish_run_id if finish_run_id else None
    if not finish_dir or not finish_dir.exists():
        dirs = sorted([d for d in finish_root.iterdir() if d.is_dir()], key=lambda d: d.name, reverse=True)
        finish_dir = dirs[0] if dirs else None

    for name in ["PUNCHLIST.md", "PUNCHLIST.csv", "SUMMARY.json"]:
        if not finish_dir or not (finish_dir / name).exists():
            return _fail_closed(out_dir, run_id, "FINISH_PLAN_ARTIFACTS_MISSING", f"Missing {name}")

    # ── E2) Write acceptance artifacts (Phase 2) ──
    try:
        from services.soma_kajabi.acceptance_artifacts import write_acceptance_artifacts
        accept_dir, accept_summary = write_acceptance_artifacts(root, run_id, phase0_dir)
        accept_rel = str(accept_dir.relative_to(root))
    except Exception as e:
        return _fail_closed(out_dir, run_id, "ACCEPTANCE_ARTIFACTS_FAILED", str(e)[:200])

    # ── E3) Fail-closed gates ──
    if not accept_summary.get("pass", True):
        return _fail_closed(
            out_dir, run_id, "MIRROR_EXCEPTIONS_NON_EMPTY",
            f"Practitioner not superset of Home above-paywall; {accept_summary.get('exceptions_count', 0)} exceptions"
        )
    offer_status, offer_pass = _check_offer_urls(root)
    if not offer_pass:
        return _fail_closed(out_dir, run_id, "OFFER_URLS_MISMATCH", offer_status)
    for name in ["final_library_snapshot.json", "video_manifest.csv", "mirror_report.json", "changelog.md"]:
        if not (accept_dir / name).exists():
            return _fail_closed(out_dir, run_id, "REQUIRED_ARTIFACTS_MISSING", f"Missing {name}")

    # ── F) Produce canonical summary artifact ──
    base_url = os.environ.get("OPENCLAW_HQ_BASE_URL", "https://hq.example.com")
    phase0_rel = str(phase0_dir.relative_to(root)) if phase0_dir else ""
    finish_rel = str(finish_dir.relative_to(root)) if finish_dir else ""

    links = {
        "connectors_status_artifact": f"{base_url}/artifacts?path=connectors",
        "phase0_artifact_dir": f"{base_url}/artifacts?path={phase0_rel}",
        "finish_plan_artifact_dir": f"{base_url}/artifacts?path={finish_rel}",
        "summary_md": f"{base_url}/artifacts?path=artifacts/soma_kajabi/auto_finish/{run_id}/SUMMARY.md",
        "acceptance_dir": f"{base_url}/artifacts?path={accept_rel}",
        "final_library_snapshot": f"{base_url}/artifacts?path={accept_rel}/final_library_snapshot.json",
        "video_manifest": f"{base_url}/artifacts?path={accept_rel}/video_manifest.csv",
        "mirror_report": f"{base_url}/artifacts?path={accept_rel}/mirror_report.json",
        "changelog": f"{base_url}/artifacts?path={accept_rel}/changelog.md",
    }
    if capture_run_id:
        links["capture_artifact_dir"] = f"{base_url}/artifacts?path=artifacts/soma_kajabi/capture_interactive/{capture_run_id}"

    summary_json = {
        "ok": True,
        "run_id": run_id,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "run_ids": {
            "connectors_status": connectors_result,
            "phase0": phase0_run_id,
            "finish_plan": finish_run_id,
            "capture": capture_run_id,
        },
        "artifact_dirs": {
            "phase0": phase0_rel,
            "finish_plan": finish_rel,
            "auto_finish": f"artifacts/soma_kajabi/auto_finish/{run_id}",
            "acceptance": accept_rel,
        },
        "snapshot_counts": {
            "home_modules": home_modules,
            "home_lessons": home_lessons,
            "practitioner_lessons": pract_lessons,
        },
        "acceptance": {
            "pass": accept_summary.get("pass", True),
            "exceptions_count": accept_summary.get("exceptions_count", 0),
            "offer_urls": offer_status,
        },
        "links": links,
        "next_actions": [
            "Connect Gmail later (optional) for video manifest harvest",
            "Execute punchlist from PUNCHLIST.md",
        ],
    }

    (out_dir / "SUMMARY.json").write_text(json.dumps(summary_json, indent=2))
    (out_dir / "LINKS.json").write_text(json.dumps(links, indent=2))

    next_actions = "\n".join(f"- {a}" for a in summary_json["next_actions"])
    acc_pass = accept_summary.get("pass", True)
    acc_table = f"""| Check | Status |
|-------|--------|
| Mirror (Home→Practitioner) | {"PASS" if acc_pass else "FAIL"} |
| Offer URLs | {offer_status} |
| Final Library Snapshot | [View]({links["final_library_snapshot"]}) |
| Video Manifest | [View]({links["video_manifest"]}) |
| Mirror Report | [View]({links["mirror_report"]}) |
| Changelog | [View]({links["changelog"]}) |"""
    summary_md = f"""# Auto-Finish Soma — PASS

**Run ID**: {run_id}
**Timestamp**: {summary_json["timestamp_utc"]}

## Snapshot Counts
- Home modules: {home_modules}
- Home lessons: {home_lessons}
- Practitioner lessons: {pract_lessons}

## Artifact Dirs
- Phase0: `{phase0_rel}`
- Finish Plan: `{finish_rel}`
- Acceptance: `{accept_rel}`

## Run IDs
- Phase0: {phase0_run_id}
- Finish Plan: {finish_run_id}
{f'- Capture (Cloudflare): {capture_run_id}' if capture_run_id else ''}

## Acceptance Checklist
{acc_table}

## Next Actions
{next_actions}

## Links
- [Open Summary]({links["summary_md"]})
- [Phase0 Artifacts]({links["phase0_artifact_dir"]})
- [Finish Plan Artifacts]({links["finish_plan_artifact_dir"]})
- [Acceptance Artifacts]({links["acceptance_dir"]})
"""
    (out_dir / "SUMMARY.md").write_text(summary_md)

    # Update project_state for HQ tile
    state_path = root / "config" / "project_state.json"
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text())
            projects = state.setdefault("projects", {})
            sk = projects.setdefault("soma_kajabi", {})
            sk["last_auto_finish_status"] = "PASS"
            sk["last_auto_finish_run_id"] = run_id
            sk["last_auto_finish_artifact_dir"] = f"artifacts/soma_kajabi/auto_finish/{run_id}"
            state_path.write_text(json.dumps(state, indent=2))
        except (OSError, json.JSONDecodeError):
            pass

    print(json.dumps(summary_json))
    return 0


if __name__ == "__main__":
    sys.exit(main())
