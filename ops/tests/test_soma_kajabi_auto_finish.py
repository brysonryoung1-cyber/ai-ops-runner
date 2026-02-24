"""Unit tests for soma_kajabi_auto_finish orchestrator."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_action_registered():
    """Action soma_kajabi_auto_finish is in action_registry."""
    reg = REPO_ROOT / "config" / "action_registry.json"
    assert reg.exists()
    data = json.loads(reg.read_text())
    ids = [a["id"] for a in data["actions"]]
    assert "soma_kajabi_auto_finish" in ids


def test_script_exists():
    """Orchestrator script exists and has expected content."""
    script = REPO_ROOT / "ops" / "scripts" / "soma_kajabi_auto_finish.py"
    assert script.exists()
    content = script.read_text()
    assert "auto_finish" in content
    assert "KAJABI_CLOUDFLARE_BLOCKED" in content
    assert "SUMMARY.json" in content


def test_phase0_success_path_produces_summary(tmp_path):
    """Phase0 success path (mocked) produces SUMMARY.md/json with expected structure."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "config").mkdir()
    (root / "artifacts" / "soma_kajabi" / "phase0").mkdir(parents=True)
    (root / "artifacts" / "soma_kajabi" / "zane_finish_plan").mkdir(parents=True)
    (root / "artifacts" / "soma_kajabi" / "auto_finish").mkdir(parents=True)

    phase0_dir = root / "artifacts" / "soma_kajabi" / "phase0" / "phase0_20250101T120000Z_abc12345"
    phase0_dir.mkdir()
    # Practitioner must be superset of Home above-paywall (mirror pass)
    (phase0_dir / "kajabi_library_snapshot.json").write_text(
        json.dumps({
            "home": {"modules": ["M1"], "lessons": [{"module_name": "M1", "title": "L1", "above_paywall": "yes"}]},
            "practitioner": {"modules": ["M1"], "lessons": [{"module_name": "M1", "title": "L1"}, {"module_name": "M1", "title": "P1"}]},
        })
    )
    (phase0_dir / "video_manifest.csv").write_text("email_id,subject,file_name,sha256,rough_topic,proposed_module,proposed_lesson_title,proposed_description,status\n")
    finish_dir = root / "artifacts" / "soma_kajabi" / "zane_finish_plan" / "zane_20250101T120100Z_def67890"
    finish_dir.mkdir()
    (finish_dir / "PUNCHLIST.md").write_text("# Punchlist\n")
    (finish_dir / "PUNCHLIST.csv").write_text("id,category,priority,title\n")
    (finish_dir / "SUMMARY.json").write_text('{"ok":true,"run_id":"zane_20250101T120100Z_def67890"}')

    storage = tmp_path / "storage.json"
    storage.write_text('{"cookies":[]}')

    # Ensure services.soma_kajabi.acceptance_artifacts is importable (from REPO_ROOT)
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    spec = importlib.util.spec_from_file_location(
        "soma_kajabi_auto_finish",
        REPO_ROOT / "ops" / "scripts" / "soma_kajabi_auto_finish.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["soma_kajabi_auto_finish"] = mod

    def mock_run(cmd, timeout=600, stream_stderr=False):
        cmd_str = " ".join(str(x) for x in cmd)
        if "connectors_status" in cmd_str:
            return 0, json.dumps({"kajabi": "connected"})
        if "phase0_runner" in cmd_str:
            return 0, json.dumps({"ok": True, "run_id": phase0_dir.name})
        if "zane_finish_plan" in cmd_str:
            return 0, json.dumps({"ok": True, "run_id": finish_dir.name})
        return 1, "{}"

    def mock_run_exit_node(cmd, timeout):
        return 0, json.dumps({"ok": True, "run_id": phase0_dir.name})

    spec.loader.exec_module(mod)
    mod.STORAGE_STATE_PATH = storage
    mod.EXIT_NODE_CONFIG = tmp_path / "nonexistent_exit_node.txt"
    mod._repo_root = lambda: root
    mod._run = mock_run
    mod._run_with_exit_node = mock_run_exit_node

    rc = mod.main()
    assert rc == 0

    auto_dirs = list((root / "artifacts" / "soma_kajabi" / "auto_finish").iterdir())
    assert len(auto_dirs) >= 1
    out_dir = auto_dirs[0]
    assert (out_dir / "SUMMARY.json").exists()
    assert (out_dir / "SUMMARY.md").exists()
    assert (out_dir / "LINKS.json").exists()
    summary = json.loads((out_dir / "SUMMARY.json").read_text())
    assert summary["ok"] is True
    assert "snapshot_counts" in summary
    assert summary["snapshot_counts"]["home_modules"] == 1
    assert "acceptance" in summary
    assert summary["acceptance"]["pass"] is True
    run_id = summary["run_id"]
    accept_dir = root / "artifacts" / "soma_kajabi" / "acceptance" / run_id
    assert accept_dir.exists()
    assert (accept_dir / "final_library_snapshot.json").exists()
    assert (accept_dir / "video_manifest.csv").exists()
    assert (accept_dir / "mirror_report.json").exists()
    assert (accept_dir / "changelog.md").exists()


def test_storage_state_missing_fails_closed(tmp_path):
    """Preflight fails when Kajabi storage_state is missing."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "config").mkdir()
    (root / "artifacts" / "soma_kajabi" / "auto_finish").mkdir(parents=True)
    missing = tmp_path / "nonexistent.json"
    assert not missing.exists()

    spec = importlib.util.spec_from_file_location(
        "soma_kajabi_auto_finish",
        REPO_ROOT / "ops" / "scripts" / "soma_kajabi_auto_finish.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.STORAGE_STATE_PATH = missing
    mod._repo_root = lambda: root

    rc = mod.main()
    assert rc == 1

    auto_dirs = list((root / "artifacts" / "soma_kajabi" / "auto_finish").iterdir())
    assert len(auto_dirs) == 1
    summary = json.loads((auto_dirs[0] / "SUMMARY.json").read_text())
    assert summary["ok"] is False
    assert "KAJABI_STORAGE_STATE_MISSING" in summary.get("error_class", "")
