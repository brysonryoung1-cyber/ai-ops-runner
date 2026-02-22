"""Tests for soma_zane_finish_plan punchlist action."""

from __future__ import annotations

import csv
import json
import os
import subprocess
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def test_zane_finish_plan_produces_artifacts():
    """Zane finish plan produces PUNCHLIST.md, PUNCHLIST.csv, SUMMARY.json given Phase0 fixture."""
    root = _repo_root()
    # Use real repo root so config/project_state.json exists; write fixture to artifacts
    phase0_base = root / "artifacts" / "soma_kajabi" / "phase0"
    phase0_base.mkdir(parents=True, exist_ok=True)
    run_dir = phase0_base / "phase0_20260222T120000Z_test1234"
    run_dir.mkdir(parents=True, exist_ok=True)

    snapshot = {
        "captured_at": "2026-02-22T12:00:00Z",
        "run_id": "phase0_20260222T120000Z_test1234",
        "mode": "storage_state",
        "home": {"modules": ["M1", "M2"], "lessons": [{"title": "L1"}, {"title": "L2"}]},
        "practitioner": {"modules": ["M1"], "lessons": [{"title": "L1"}]},
    }
    (run_dir / "kajabi_library_snapshot.json").write_text(json.dumps(snapshot, indent=2))

    harvest_skipped = {"gmail_status": "skipped", "gmail_reason": "oauth token not found at /etc/ai-ops-runner/secrets/soma_kajabi/gmail_oauth.json"}
    (run_dir / "gmail_harvest.jsonl").write_text(json.dumps(harvest_skipped) + "\n")

    (run_dir / "video_manifest.csv").write_text("email_id,subject,file_name,sha256,rough_topic,proposed_module,proposed_lesson_title,proposed_description,status\n")

    try:
        env = {**os.environ, "OPENCLAW_REPO_ROOT": str(root), "RUN_ID": "phase0_20260222T120000Z_test1234"}
        r = subprocess.run(
            ["python3", "-m", "services.soma_kajabi.zane_finish_plan"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        assert r.returncode == 0, f"stdout={r.stdout} stderr={r.stderr}"

        out = json.loads(r.stdout.strip())
        assert out.get("ok") is True
        assert "run_id" in out
        assert "artifact_paths" in out
        assert "next_10_actions" in out

        zane_base = root / "artifacts" / "soma_kajabi" / "zane_finish_plan"
        run_dirs = [d for d in zane_base.iterdir() if d.is_dir()]
        assert run_dirs, "Expected at least one zane_finish_plan run dir"
        latest = max(run_dirs, key=lambda d: d.name)

        assert (latest / "PUNCHLIST.md").exists()
        assert (latest / "PUNCHLIST.csv").exists()
        assert (latest / "SUMMARY.json").exists()

        summary = json.loads((latest / "SUMMARY.json").read_text())
        assert summary.get("gmail_skipped") is True
        assert summary.get("counts", {}).get("home_modules") == 2
        assert len(summary.get("next_10_actions", [])) <= 10

        # First 3 should be kajabi_ui
        next_10 = summary.get("next_10_actions", [])
        first_3 = next_10[:3]
        for a in first_3:
            assert a.get("kajabi_ui") is True, f"First 3 must be Kajabi UI: {a}"

        # Gmail-dependent items should be blocked
        punchlist_csv = list(csv.DictReader((latest / "PUNCHLIST.csv").open(encoding="utf-8")))
        blocked = [r for r in punchlist_csv if r.get("blocked", "").lower() == "true"]
        assert len(blocked) >= 1, "Expected at least one BLOCKED item when Gmail skipped"
    finally:
        if run_dir.exists():
            for f in run_dir.iterdir():
                f.unlink()
            run_dir.rmdir()


def test_zane_finish_plan_blocks_a1_a2_a3_when_snapshot_empty():
    """When Phase0 snapshot is empty (all modules+lessons zero), A1/A2/A3 must be BLOCKED."""
    import uuid

    root = _repo_root()
    run_id = f"phase0_20260222T130000Z_empty_{uuid.uuid4().hex[:8]}"
    phase0_base = root / "artifacts" / "soma_kajabi" / "phase0"
    phase0_base.mkdir(parents=True, exist_ok=True)
    run_dir = phase0_base / run_id
    run_dir.mkdir(parents=True)

    # Empty snapshot: all modules and lessons empty
    snapshot = {
        "captured_at": "2026-02-22T13:00:00Z",
        "run_id": run_id,
        "mode": "storage_state",
        "home": {"modules": [], "lessons": []},
        "practitioner": {"modules": [], "lessons": []},
    }
    (run_dir / "kajabi_library_snapshot.json").write_text(json.dumps(snapshot, indent=2))
    (run_dir / "gmail_harvest.jsonl").write_text(json.dumps({"gmail_status": "skipped"}) + "\n")
    (run_dir / "video_manifest.csv").write_text("email_id,subject,file_name,sha256,rough_topic,proposed_module,proposed_lesson_title,proposed_description,status\n")

    try:
        env = {**os.environ, "OPENCLAW_REPO_ROOT": str(root), "RUN_ID": run_id}
        r = subprocess.run(
            ["python3", "-m", "services.soma_kajabi.zane_finish_plan"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        assert r.returncode == 0, f"stdout={r.stdout} stderr={r.stderr}"

        out_json = json.loads(r.stdout.strip())
        zane_run_id = out_json.get("run_id")
        assert zane_run_id, "zane_finish_plan must return run_id"

        zane_base = root / "artifacts" / "soma_kajabi" / "zane_finish_plan"
        out_dir = zane_base / zane_run_id
        assert out_dir.exists(), f"Expected output dir {out_dir}"
        punchlist_csv = list(csv.DictReader((out_dir / "PUNCHLIST.csv").open(encoding="utf-8")))
        a_items = [r for r in punchlist_csv if r.get("id", "").startswith("A")]
        a1_a2_a3 = [r for r in a_items if r.get("id") in ("A1", "A2", "A3")]
        for row in a1_a2_a3:
            assert row.get("blocked", "").lower() == "true", f"Expected A1/A2/A3 blocked when snapshot empty: {row}"
            assert "soma_kajabi_discover" in (row.get("blocked_reason") or "")
    finally:
        if run_dir.exists():
            for f in run_dir.iterdir():
                f.unlink()
            run_dir.rmdir()
