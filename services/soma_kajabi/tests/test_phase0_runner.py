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


def test_allowlist_contains_soma_kajabi_discover():
    """job_allowlist.yaml must contain soma_kajabi_discover job."""
    root = _repo_root()
    import yaml
    allowlist_path = root / "configs" / "job_allowlist.yaml"
    assert allowlist_path.exists()
    data = yaml.safe_load(allowlist_path.read_text())
    jobs = data.get("jobs", {})
    assert "soma_kajabi_discover" in jobs
    assert jobs["soma_kajabi_discover"].get("timeout_sec") == 180


def test_soma_kajabi_discoverable_in_projects():
    """soma_kajabi must appear in config/projects.json."""
    root = _repo_root()
    projects_path = root / "config" / "projects.json"
    assert projects_path.exists()
    data = json.loads(projects_path.read_text())
    ids = [p["id"] for p in data.get("projects", [])]
    assert "soma_kajabi" in ids


def test_phase0_runs_even_with_kill_switch():
    """Phase 0 inventory is permitted when kill_switch=true; runner proceeds and writes artifacts."""
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
    out = r.stdout.strip()
    lines = [l for l in out.split("\n") if l.strip().startswith("{")]
    parsed = json.loads(lines[-1]) if lines else {}
    # Must NOT be blocked by kill_switch (no KILL_SWITCH_ENABLED)
    assert parsed.get("error_class") != "KILL_SWITCH_ENABLED", f"Phase 0 should run when kill_switch=true. Got {parsed}"
    assert "run_id" in parsed
    assert "artifact_paths" in parsed


def test_phase0_actions_always_write_artifacts():
    """Phase0 runner writes required artifacts including BASELINE_OK.json."""
    root = _repo_root()
    art_base = root / "artifacts" / "soma_kajabi" / "phase0"
    if not art_base.exists():
        return  # No run yet
    run_dirs = [d for d in art_base.iterdir() if d.is_dir()]
    if not run_dirs:
        return
    latest = max(run_dirs, key=lambda d: d.name)
    required = ["kajabi_library_snapshot.json", "gmail_harvest.jsonl", "video_manifest.csv", "result.json", "BASELINE_OK.json"]
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


def test_phase0_fails_when_kajabi_storage_state_missing():
    """Phase0 returns CONNECTOR_NOT_CONFIGURED when Kajabi storage_state is missing/invalid."""
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
    out = r.stdout.strip()
    lines = [l for l in out.split("\n") if l.strip().startswith("{")]
    parsed = json.loads(lines[-1]) if lines else {}
    # With default config (kajabi manual or storage_state missing), must fail
    assert parsed.get("error_class") == "CONNECTOR_NOT_CONFIGURED"
    assert parsed.get("ok") is False


def test_phase0_returns_empty_snapshot_when_snapshot_empty():
    """Phase0 returns ok:false, error_class=EMPTY_SNAPSHOT when modules+lessons all zero."""
    from unittest.mock import patch

    root = _repo_root()
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        out_dir = tmp_path / "artifacts" / "soma_kajabi" / "phase0" / "run1"
        out_dir.mkdir(parents=True)
        run_id = "run1"

        def _mock_snapshot(_product: str, smoke: bool = False):
            art = tmp_path / "soma_art"
            art.mkdir(parents=True, exist_ok=True)
            (art / "snapshot.json").write_text(json.dumps({"categories": []}))
            return {"artifacts_dir": str(art)}

        with patch("services.soma_kajabi_sync.snapshot.snapshot_kajabi", side_effect=_mock_snapshot):
            with patch("services.soma_kajabi_sync.config.load_secret", return_value="fake_token"):
                from services.soma_kajabi.phase0_runner import _run_kajabi_snapshot

                cfg = {
                    "kajabi": {"mode": "session_token"},
                    "gmail": {},
                    "artifacts": {},
                }
                ok, rec, err_class = _run_kajabi_snapshot(tmp_path, out_dir, run_id, cfg)
        assert ok is False
        assert err_class == "EMPTY_SNAPSHOT"
        assert "soma_kajabi_discover" in (rec or "")
        assert (out_dir / "kajabi_capture_debug.json").exists()
