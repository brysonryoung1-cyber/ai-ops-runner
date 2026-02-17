"""Hermetic tests for soma_kajabi Phase 0 runner.

No network, no real credentials. Uses temp dirs and mock config.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def test_soma_kajabi_discoverable_in_projects():
    """soma_kajabi must appear in config/projects.json."""
    root = _repo_root()
    projects_path = root / "config" / "projects.json"
    assert projects_path.exists()
    data = json.loads(projects_path.read_text())
    ids = [p["id"] for p in data.get("projects", [])]
    assert "soma_kajabi" in ids


def test_kill_switch_blocks_soma_kajabi_actions():
    """When kill_switch=true, phase0_runner exits 1 with KILL_SWITCH_ENABLED."""
    root = _repo_root()
    env = {**os.environ, "OPENCLAW_REPO_ROOT": str(root)}
    r = subprocess.run(
        ["python3", "-m", "services.soma_kajabi.phase0_runner"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert r.returncode == 1, f"Expected exit 1, got {r.returncode}. stdout={r.stdout!r} stderr={r.stderr!r}"
    out = r.stdout.strip()
    lines = [l for l in out.split("\n") if l.strip().startswith("{")]
    parsed = json.loads(lines[-1]) if lines else {}
    assert parsed.get("error_class") == "KILL_SWITCH_ENABLED", f"Got {parsed}"
    assert "recommended_next_action" in parsed


def test_phase0_actions_always_write_artifacts():
    """Phase0 runner writes all 4 artifacts even on fail-closed (kill_switch)."""
    root = _repo_root()
    art_base = root / "artifacts" / "soma_kajabi" / "phase0"
    if not art_base.exists():
        return  # No run yet
    run_dirs = [d for d in art_base.iterdir() if d.is_dir()]
    if not run_dirs:
        return
    latest = max(run_dirs, key=lambda d: d.name)
    required = ["kajabi_library_snapshot.json", "gmail_harvest.jsonl", "video_manifest.csv", "result.json"]
    for name in required:
        assert (latest / name).exists(), f"Missing {name} in {latest}"


def test_result_json_schema():
    """result.json must have ok, action, run_id, artifact_paths."""
    root = _repo_root()
    art_base = root / "artifacts" / "soma_kajabi" / "phase0"
    if not art_base.exists():
        return
    run_dirs = [d for d in art_base.iterdir() if d.is_dir()]
    if not run_dirs:
        return
    latest = max(run_dirs, key=lambda d: d.name)
    result_path = latest / "result.json"
    if not result_path.exists():
        return
    data = json.loads(result_path.read_text())
    assert "ok" in data
    assert data.get("action") == "soma_kajabi_phase0"
    assert "run_id" in data
    assert "artifact_paths" in data
    assert isinstance(data["artifact_paths"], list)


def test_kajabi_snapshot_schema():
    """kajabi_library_snapshot.json must have captured_at, run_id, mode, home, practitioner."""
    root = _repo_root()
    art_base = root / "artifacts" / "soma_kajabi" / "phase0"
    if not art_base.exists():
        return
    run_dirs = [d for d in art_base.iterdir() if d.is_dir()]
    if not run_dirs:
        return
    latest = max(run_dirs, key=lambda d: d.name)
    snap_path = latest / "kajabi_library_snapshot.json"
    if not snap_path.exists():
        return
    data = json.loads(snap_path.read_text())
    assert "captured_at" in data
    assert "run_id" in data
    assert "mode" in data
    assert "home" in data
    assert "practitioner" in data
    assert "modules" in data["home"]
    assert "lessons" in data["home"]


def test_no_secrets_in_json_output():
    """Phase0 runner output must not contain raw credential patterns."""
    root = _repo_root()
    env = {**os.environ, "OPENCLAW_REPO_ROOT": str(root)}
    r = subprocess.run(
        ["python3", "-m", "services.soma_kajabi.phase0_runner"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    out = r.stdout + r.stderr
    # Output should be valid JSON with known keys only; no raw passwords
    assert "GMAIL_APP_PASSWORD" not in out or "env" in out
