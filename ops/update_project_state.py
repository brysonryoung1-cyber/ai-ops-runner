#!/usr/bin/env python3
"""
Update OpenClaw project state (canonical brain).

- Reads live signals: git HEAD, last doctor/guard result, LLM config (no secrets).
- Updates config/project_state.json (safe fields only).
- Updates docs/OPENCLAW_CURRENT.md and docs/OPENCLAW_NEXT.md from templates.
- Writes artifacts/state/<timestamp>/state.json (redacted snapshot).

Run from repo root or ops/; called by doctor and deploy.
No secrets in any output.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def repo_root() -> Path:
    """Repo root: OPS_DIR/.. or cwd."""
    ops_dir = os.environ.get("OPS_DIR")
    if ops_dir:
        return Path(ops_dir).resolve().parent
    # Assume we're in repo root or ops/
    cwd = Path.cwd().resolve()
    if (cwd / "config" / "project_state.json").exists():
        return cwd
    if (cwd.parent / "config" / "project_state.json").exists():
        return cwd.parent
    return cwd


def git_short_head(root: Path) -> str | None:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode == 0 and r.stdout:
            return r.stdout.strip()
    except Exception:
        pass
    return None


def last_doctor_result(root: Path) -> tuple[str | None, str | None]:
    """Return (timestamp_iso, result 'PASS'|'FAIL') from latest artifacts/doctor run."""
    base = root / "artifacts" / "doctor"
    if not base.exists():
        return None, None
    dirs = sorted([d.name for d in base.iterdir() if d.is_dir()], reverse=True)
    for d in dirs:
        j = base / d / "doctor.json"
        if j.exists():
            try:
                data = json.loads(j.read_text())
                ts = data.get("timestamp")
                res = data.get("result")
                if res in ("PASS", "FAIL"):
                    return ts, res
            except Exception:
                continue
    return None, None


def last_guard_result(root: Path) -> str | None:
    """Return last guard result (PASS/FAIL) from /var/log/openclaw_guard.log or env."""
    # On VPS the log is at /var/log; we may not have read access when run as non-root.
    log_path = Path("/var/log/openclaw_guard.log")
    if log_path.exists():
        try:
            text = log_path.read_text()
            for line in reversed(text.strip().split("\n")):
                if "RESULT: PASS" in line:
                    return "PASS"
                if "RESULT: FAIL" in line:
                    return "FAIL"
        except Exception:
            pass
    return os.environ.get("OPENCLAW_LAST_GUARD_RESULT")


def llm_config_safe(root: Path) -> tuple[str, str, str, str]:
    """Primary provider/model and fallback provider/model from config/llm.json (no secrets)."""
    primary_p, primary_m = "openai", "gpt-4o-mini"
    fallback_p, fallback_m = "mistral", "labs-devstral-small-2512"
    cfg = root / "config" / "llm.json"
    if not cfg.exists():
        return primary_p, primary_m, fallback_p, fallback_m
    try:
        data = json.loads(cfg.read_text())
        defaults = data.get("defaults") or {}
        review = defaults.get("review") or {}
        if isinstance(review, dict):
            primary_p = review.get("provider") or primary_p
            primary_m = review.get("model") or primary_m
        rf = data.get("reviewFallback") or {}
        if isinstance(rf, dict):
            fallback_p = rf.get("provider") or fallback_p
            fallback_m = rf.get("model") or fallback_m
    except Exception:
        pass
    return primary_p, primary_m, fallback_p, fallback_m


def load_current_state(root: Path) -> dict:
    state_path = root / "config" / "project_state.json"
    if state_path.exists():
        try:
            return json.loads(state_path.read_text())
        except Exception:
            pass
    return {
        "project_name": "OpenClaw",
        "goal_summary": "Self-updating project brain; repo + HQ canonical; no ChatGPT memory reliance.",
        "last_verified_vps_head": None,
        "last_deploy_timestamp": None,
        "last_guard_result": None,
        "last_doctor_result": None,
        "llm_primary_provider": "openai",
        "llm_primary_model": "gpt-4o-mini",
        "llm_fallback_provider": "mistral",
        "llm_fallback_model": "labs-devstral-small-2512",
        "zane_agent_phase": 0,
        "next_action_id": "",
        "next_action_text": "",
        "ui_accepted": False,
        "ui_accepted_at": None,
        "ui_accepted_commit": None,
    }


def main() -> int:
    root = repo_root()
    config_dir = root / "config"
    docs_dir = root / "docs"
    artifacts_dir = root / "artifacts"

    # Live signals (no secrets)
    head = git_short_head(root)
    doctor_ts, doctor_res = last_doctor_result(root)
    guard_res = last_guard_result(root)
    primary_p, primary_m, fallback_p, fallback_m = llm_config_safe(root)

    state = load_current_state(root)
    # Only overwrite fields we can derive; preserve deploy/next_action if passed in via env
    state["last_verified_vps_head"] = head or state.get("last_verified_vps_head")
    state["last_doctor_result"] = doctor_res or state.get("last_doctor_result")
    state["last_guard_result"] = guard_res or state.get("last_guard_result")
    state["llm_primary_provider"] = primary_p
    state["llm_primary_model"] = primary_m
    state["llm_fallback_provider"] = fallback_p
    state["llm_fallback_model"] = fallback_m
    # ui_accepted, ui_accepted_at, ui_accepted_commit are preserved from load_current_state (set manually)
    if os.environ.get("OPENCLAW_DEPLOY_TIMESTAMP"):
        state["last_deploy_timestamp"] = os.environ.get("OPENCLAW_DEPLOY_TIMESTAMP")
    if os.environ.get("OPENCLAW_NEXT_ACTION_ID"):
        state["next_action_id"] = os.environ.get("OPENCLAW_NEXT_ACTION_ID")
    if os.environ.get("OPENCLAW_NEXT_ACTION_TEXT"):
        state["next_action_text"] = os.environ.get("OPENCLAW_NEXT_ACTION_TEXT")

    # Persist config/project_state.json
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "project_state.json").write_text(
        json.dumps(state, indent=2), encoding="utf-8"
    )

    # Update docs/OPENCLAW_CURRENT.md from template
    current_md = f"""# OpenClaw — Current State

*(Auto-updated by ops/update_project_state.py from config/project_state.json.)*

- **Project**: {state.get('project_name', 'OpenClaw')} (ai-ops-runner)
- **Goal summary**: {state.get('goal_summary', '')}
- **Last verified VPS HEAD**: {state.get('last_verified_vps_head') or '—'}
- **Last deploy**: {state.get('last_deploy_timestamp') or '—'}
- **Last doctor**: {state.get('last_doctor_result') or '—'}
- **Last guard**: {state.get('last_guard_result') or '—'}
- **Zane phase**: {state.get('zane_agent_phase', 0)}
- **UI accepted**: {state.get('ui_accepted')} (at: {state.get('ui_accepted_at') or '—'}, commit: {state.get('ui_accepted_commit') or '—'})
- **LLM primary**: {state.get('llm_primary_provider', 'openai')} / {state.get('llm_primary_model', 'gpt-4o-mini')}
- **LLM fallback**: {state.get('llm_fallback_provider', 'mistral')} / {state.get('llm_fallback_model', 'labs-devstral-small-2512')}

## Definition-of-Done (DoD)

- **DoD script**: `ops/dod_production.sh` — executable checks: hostd /health, /api/ai-status, /api/llm/status, /api/project/state, POST /api/exec action=doctor (PASS), /api/artifacts/list dirs > 0, no hard-fail strings (ENOENT, spawn ssh, Host Executor Unreachable).
- **Pipeline enforcement**: `ops/deploy_pipeline.sh` runs DoD at Step 5b (after verify_production); deploy fails if DoD exits non-zero. No bypass flags.
- **Proof artifacts**: `artifacts/dod/<run_id>/dod_result.json` (redacted; no secrets). Linked from deploy_result.artifacts.dod_result and served via GET `/api/dod/last`.
"""
    (docs_dir / "OPENCLAW_CURRENT.md").write_text(current_md, encoding="utf-8")

    # Update docs/OPENCLAW_NEXT.md (single next action)
    next_text = state.get("next_action_text") or "No next action set."
    (docs_dir / "OPENCLAW_NEXT.md").write_text(
        f"# Next Action\n\n{next_text}\n", encoding="utf-8"
    )

    # Write artifacts/state/<timestamp>/state.json (redacted snapshot)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    state_artifacts = artifacts_dir / "state" / ts
    state_artifacts.mkdir(parents=True, exist_ok=True)
    snapshot = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "project_name": state.get("project_name"),
        "goal_summary": state.get("goal_summary"),
        "last_verified_vps_head": state.get("last_verified_vps_head"),
        "last_deploy_timestamp": state.get("last_deploy_timestamp"),
        "last_doctor_result": state.get("last_doctor_result"),
        "last_guard_result": state.get("last_guard_result"),
        "llm_primary_provider": state.get("llm_primary_provider"),
        "llm_primary_model": state.get("llm_primary_model"),
        "llm_fallback_provider": state.get("llm_fallback_provider"),
        "llm_fallback_model": state.get("llm_fallback_model"),
        "zane_agent_phase": state.get("zane_agent_phase"),
        "next_action_id": state.get("next_action_id"),
        "next_action_text": state.get("next_action_text"),
        "ui_accepted": state.get("ui_accepted"),
        "ui_accepted_at": state.get("ui_accepted_at"),
        "ui_accepted_commit": state.get("ui_accepted_commit"),
    }
    (state_artifacts / "state.json").write_text(
        json.dumps(snapshot, indent=2), encoding="utf-8"
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
