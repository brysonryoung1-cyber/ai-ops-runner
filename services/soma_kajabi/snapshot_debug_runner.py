#!/usr/bin/env python3
"""soma_kajabi_snapshot_debug — Dry-run snapshot only, dumps debug artifacts.

Runs Kajabi snapshot for Home + Practitioner with full debug output:
  - Screenshot(s) of admin page(s)
  - page.html for each product
  - kajabi_<slug>_debug.json with timings, selectors, counts

No Phase0 steps (no Gmail, no video manifest). Use as canary when Kajabi changes.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def main() -> int:
    root = _repo_root()
    run_id = f"snapshot_debug_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"
    out_dir = root / "artifacts" / "soma_kajabi" / "snapshot_debug" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        from services.soma_kajabi.connector_config import KAJABI_STORAGE_STATE_PATH, load_soma_kajabi_config
        from services.soma_kajabi_sync.snapshot import (
            KajabiSnapshotError,
            _validate_storage_state_has_kajabi_cookies,
            snapshot_kajabi,
        )
    except ImportError as e:
        doc = {
            "ok": False,
            "run_id": run_id,
            "artifact_dir": str(out_dir),
            "error_class": "IMPORT_ERROR",
            "recommended_next_action": str(e),
        }
        (out_dir / "result.json").write_text(json.dumps(doc, indent=2))
        print(json.dumps({"ok": False, "error_class": "IMPORT_ERROR", "artifact_dir": str(out_dir)}))
        return 1

    cfg, config_error = load_soma_kajabi_config(root)
    if config_error:
        doc = {
            "ok": False,
            "run_id": run_id,
            "artifact_dir": str(out_dir),
            "error_class": "CONFIG_INVALID",
            "recommended_next_action": config_error,
        }
        (out_dir / "result.json").write_text(json.dumps(doc, indent=2))
        print(json.dumps({"ok": False, "error_class": "CONFIG_INVALID", "artifact_dir": str(out_dir)}))
        return 1

    kajabi_cfg = cfg.get("kajabi") or {}
    mode = kajabi_cfg.get("mode", "manual")
    if mode == "manual":
        doc = {
            "ok": False,
            "run_id": run_id,
            "artifact_dir": str(out_dir),
            "error_class": "CONNECTOR_NOT_CONFIGURED",
            "recommended_next_action": "Set kajabi.mode=storage_state in config/projects/soma_kajabi.json",
        }
        (out_dir / "result.json").write_text(json.dumps(doc, indent=2))
        print(json.dumps({"ok": False, "error_class": "CONNECTOR_NOT_CONFIGURED", "artifact_dir": str(out_dir)}))
        return 1

    storage_state_path = Path(
        kajabi_cfg.get("storage_state_secret_ref") or str(KAJABI_STORAGE_STATE_PATH)
    )
    valid, msg = _validate_storage_state_has_kajabi_cookies(storage_state_path)
    if not valid:
        doc = {
            "ok": False,
            "run_id": run_id,
            "artifact_dir": str(out_dir),
            "error_class": "KAJABI_STORAGE_STATE_INVALID",
            "recommended_next_action": msg,
        }
        (out_dir / "result.json").write_text(json.dumps(doc, indent=2))
        print(json.dumps({"ok": False, "error_class": "KAJABI_STORAGE_STATE_INVALID", "artifact_dir": str(out_dir)}))
        return 1

    try:
        home_result = snapshot_kajabi(
            "Home User Library",
            smoke=False,
            storage_state_path=storage_state_path,
            debug_artifact_dir=out_dir,
        )
        pract_result = snapshot_kajabi(
            "Practitioner Library",
            smoke=False,
            storage_state_path=storage_state_path,
            debug_artifact_dir=out_dir,
        )
    except KajabiSnapshotError as e:
        doc = {
            "ok": False,
            "run_id": run_id,
            "artifact_dir": str(out_dir),
            "error_class": e.error_class,
            "recommended_next_action": e.message,
        }
        (out_dir / "result.json").write_text(json.dumps(doc, indent=2))
        print(json.dumps({"ok": False, "error_class": e.error_class, "artifact_dir": str(out_dir)}))
        return 1

    home_cats = []
    pract_cats = []
    if isinstance(home_result, dict) and home_result.get("artifacts_dir"):
        snap_path = Path(home_result["artifacts_dir"]) / "snapshot.json"
        if snap_path.exists():
            home_cats = json.loads(snap_path.read_text()).get("categories", [])
    if isinstance(pract_result, dict) and pract_result.get("artifacts_dir"):
        snap_path = Path(pract_result["artifacts_dir"]) / "snapshot.json"
        if snap_path.exists():
            pract_cats = json.loads(snap_path.read_text()).get("categories", [])

    home_mods = len([c.get("name", "") for c in home_cats])
    home_items = sum(len(c.get("items", [])) for c in home_cats)
    pract_mods = len([c.get("name", "") for c in pract_cats])
    pract_items = sum(len(c.get("items", [])) for c in pract_cats)

    doc = {
        "ok": True,
        "run_id": run_id,
        "artifact_dir": str(out_dir),
        "captured_at": _now_iso(),
        "home": {"categories": len(home_cats), "items": home_items},
        "practitioner": {"categories": len(pract_cats), "items": pract_items},
        "artifacts": [p.name for p in out_dir.iterdir() if p.is_file()],
    }
    (out_dir / "result.json").write_text(json.dumps(doc, indent=2))
    print(json.dumps({
        "ok": True,
        "run_id": run_id,
        "artifact_dir": str(out_dir),
        "home_categories": len(home_cats),
        "home_items": home_items,
        "practitioner_categories": len(pract_cats),
        "practitioner_items": pract_items,
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
