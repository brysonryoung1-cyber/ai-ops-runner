"""Unit tests for soma_kajabi_auto_finish orchestrator."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

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
    _setup_ops_scripts(root)
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
    _setup_ops_scripts(root)
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


def _setup_ops_scripts(root: Path):
    """Create ops/scripts with state module and novnc stub for hermetic tests."""
    scripts = root / "ops" / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    state_src = REPO_ROOT / "ops" / "scripts" / "soma_kajabi_auto_finish_state.py"
    if state_src.exists():
        (scripts / "soma_kajabi_auto_finish_state.py").write_text(state_src.read_text())
    # Ensure ops.soma.auto_finish_state_machine is importable (uses REPO_ROOT from sys.path)


def _setup_novnc_mock(root: Path):
    """Create minimal novnc_ready stub and kajabi_capture_interactive placeholder (script existence check)."""
    _setup_ops_scripts(root)
    scripts = root / "ops" / "scripts"
    (scripts / "novnc_ready.py").write_text('''
def ensure_novnc_ready(artifact_dir, run_id):
    return True, "http://test-novnc.example:6080/vnc.html?autoconnect=1", None, None
''')
    (scripts / "kajabi_capture_interactive.py").write_text("# stub for test\n")


def test_capture_interactive_failed_emits_waiting_and_resumes(tmp_path):
    """When capture_interactive throws KAJABI_CAPTURE_INTERACTIVE_FAILED -> WAITING_FOR_HUMAN emitted + poll loop entered; session_check PASS -> pipeline resumes."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "config").mkdir()
    (root / "artifacts" / "soma_kajabi" / "phase0").mkdir(parents=True)
    (root / "artifacts" / "soma_kajabi" / "zane_finish_plan").mkdir(parents=True)
    (root / "artifacts" / "soma_kajabi" / "auto_finish").mkdir(parents=True)
    _setup_novnc_mock(root)

    phase0_dir = root / "artifacts" / "soma_kajabi" / "phase0" / "phase0_20250101T120000Z_abc12345"
    phase0_dir.mkdir()
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

    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    spec = importlib.util.spec_from_file_location(
        "soma_kajabi_auto_finish",
        REPO_ROOT / "ops" / "scripts" / "soma_kajabi_auto_finish.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["soma_kajabi_auto_finish"] = mod

    phase0_call_count = 0

    def mock_run(cmd, timeout=600, stream_stderr=False):
        nonlocal phase0_call_count
        cmd_str = " ".join(str(x) for x in cmd)
        if "connectors_status" in cmd_str:
            return 0, json.dumps({"kajabi": "connected"})
        if "phase0_runner" in cmd_str:
            phase0_call_count += 1
            if phase0_call_count == 1:
                return 1, json.dumps({"ok": False, "error_class": "KAJABI_CLOUDFLARE_BLOCKED"})
            return 0, json.dumps({"ok": True, "run_id": phase0_dir.name})
        if "kajabi_capture_interactive" in cmd_str:
            return 1, json.dumps({"ok": False, "error_class": "KAJABI_CAPTURE_INTERACTIVE_FAILED", "message": "Capture failed"})
        if "zane_finish_plan" in cmd_str:
            return 0, json.dumps({"ok": True, "run_id": finish_dir.name})
        return 1, "{}"

    def mock_run_exit_node(cmd, timeout):
        return mock_run(cmd, timeout)

    def mock_run_session_check(r, v, u):
        return 0, json.dumps({"ok": True, "products_found": ["Home User Library", "Practitioner Library"]})

    spec.loader.exec_module(mod)
    mod.STORAGE_STATE_PATH = storage
    mod.EXIT_NODE_CONFIG = tmp_path / "nonexistent_exit_node.txt"
    mod._repo_root = lambda: root
    mod._run = mock_run
    mod._run_with_exit_node = mock_run_exit_node
    mod._run_session_check = mock_run_session_check
    mod._run_self_heal = lambda *a, **k: None
    mod._run_doctor_for_framebuffer = lambda *a: (True, "artifacts/novnc_debug/auto_finish_run")

    with patch.dict(os.environ, {"SOMA_KAJABI_REAUTH_POLL_TIMEOUT": "15"}):
        rc = mod.main()

    assert rc == 0
    auto_dirs = list((root / "artifacts" / "soma_kajabi" / "auto_finish").iterdir())
    assert len(auto_dirs) >= 1
    out_dir = auto_dirs[0]
    assert (out_dir / "WAITING_FOR_HUMAN.json").exists()
    wfh = json.loads((out_dir / "WAITING_FOR_HUMAN.json").read_text())
    assert wfh["status"] == "WAITING_FOR_HUMAN"
    assert "novnc_url" in wfh
    assert wfh["novnc_url"]
    assert "instruction" in wfh
    assert "resume_condition" in wfh
    assert (out_dir / "stage.json").exists()
    summary = json.loads((out_dir / "SUMMARY.json").read_text())
    assert summary["ok"] is True


def test_waiting_for_human_contract_includes_novnc_url_and_instruction(tmp_path):
    """Contract: WAITING_FOR_HUMAN output includes verified noVNC URL and instruction line."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "config").mkdir()
    (root / "artifacts" / "soma_kajabi" / "phase0").mkdir(parents=True)
    (root / "artifacts" / "soma_kajabi" / "zane_finish_plan").mkdir(parents=True)
    (root / "artifacts" / "soma_kajabi" / "auto_finish").mkdir(parents=True)
    _setup_novnc_mock(root)

    phase0_dir = root / "artifacts" / "soma_kajabi" / "phase0" / "phase0_20250101T120000Z_abc12345"
    phase0_dir.mkdir()
    (phase0_dir / "kajabi_library_snapshot.json").write_text(
        json.dumps({
            "home": {"modules": ["M1"], "lessons": [{"module_name": "M1", "title": "L1", "above_paywall": "yes"}]},
            "practitioner": {"modules": ["M1"], "lessons": [{"module_name": "M1", "title": "L1"}]},
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

    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    spec = importlib.util.spec_from_file_location(
        "soma_kajabi_auto_finish",
        REPO_ROOT / "ops" / "scripts" / "soma_kajabi_auto_finish.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["soma_kajabi_auto_finish"] = mod

    phase0_calls = 0

    def mock_run(cmd, timeout=600, stream_stderr=False):
        nonlocal phase0_calls
        cmd_str = " ".join(str(x) for x in cmd)
        if "connectors_status" in cmd_str:
            return 0, json.dumps({"kajabi": "connected"})
        if "phase0_runner" in cmd_str:
            phase0_calls += 1
            if phase0_calls == 1:
                return 1, json.dumps({"ok": False, "error_class": "KAJABI_CLOUDFLARE_BLOCKED"})
            return 0, json.dumps({"ok": True, "run_id": phase0_dir.name})
        if "kajabi_capture_interactive" in cmd_str:
            return 1, json.dumps({"ok": False})
        if "zane_finish_plan" in cmd_str:
            return 0, json.dumps({"ok": True, "run_id": finish_dir.name})
        return 1, "{}"

    def mock_run_session_check(*a):
        return 0, json.dumps({"ok": True})

    spec.loader.exec_module(mod)
    mod.STORAGE_STATE_PATH = storage
    mod.EXIT_NODE_CONFIG = tmp_path / "nonexistent_exit_node.txt"
    mod._repo_root = lambda: root
    mod._run = mock_run
    mod._run_with_exit_node = lambda c, t: mock_run(c, t)
    mod._run_session_check = mock_run_session_check
    mod._run_self_heal = lambda *a, **k: None
    mod._run_doctor_for_framebuffer = lambda *a: (True, "artifacts/novnc_debug/auto_finish_run")

    with patch.dict(os.environ, {"SOMA_KAJABI_REAUTH_POLL_TIMEOUT": "10"}):
        mod.main()

    out_dir = next((root / "artifacts" / "soma_kajabi" / "auto_finish").iterdir())
    wfh = json.loads((out_dir / "WAITING_FOR_HUMAN.json").read_text())
    assert "novnc_url" in wfh
    url = wfh["novnc_url"]
    assert isinstance(url, str)
    assert url.startswith("http://") or url.startswith("https://")
    assert ":6080" in url and "vnc.html" in url
    assert "instruction" in wfh or "instruction_line" in wfh
    instr = wfh.get("instruction") or wfh.get("instruction_line", "")
    assert len(instr) > 10
    assert "artifact_dir" in wfh, "WAITING_FOR_HUMAN must include artifact_dir for doctor run"
    assert wfh["artifact_dir"].startswith("artifacts/novnc_debug/")


def test_is_auth_needed_error_expands_to_kajabi_not_logged_in(tmp_path):
    """KAJABI_NOT_LOGGED_IN (and other auth signals) trigger WAITING_FOR_HUMAN, not hard-fail."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "config").mkdir()
    (root / "artifacts" / "soma_kajabi" / "phase0").mkdir(parents=True)
    (root / "artifacts" / "soma_kajabi" / "zane_finish_plan").mkdir(parents=True)
    (root / "artifacts" / "soma_kajabi" / "auto_finish").mkdir(parents=True)
    _setup_novnc_mock(root)

    phase0_dir = root / "artifacts" / "soma_kajabi" / "phase0" / "phase0_20250101T120000Z_abc12345"
    phase0_dir.mkdir()
    (phase0_dir / "kajabi_library_snapshot.json").write_text(
        json.dumps({
            "home": {"modules": ["M1"], "lessons": [{"module_name": "M1", "title": "L1", "above_paywall": "yes"}]},
            "practitioner": {"modules": ["M1"], "lessons": [{"module_name": "M1", "title": "L1"}]},
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

    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    spec = importlib.util.spec_from_file_location(
        "soma_kajabi_auto_finish",
        REPO_ROOT / "ops" / "scripts" / "soma_kajabi_auto_finish.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["soma_kajabi_auto_finish"] = mod

    phase0_calls = 0

    def mock_run(cmd, timeout=600, stream_stderr=False):
        nonlocal phase0_calls
        cmd_str = " ".join(str(x) for x in cmd)
        if "connectors_status" in cmd_str:
            return 0, json.dumps({"kajabi": "connected"})
        if "phase0_runner" in cmd_str:
            phase0_calls += 1
            if phase0_calls == 1:
                return 1, json.dumps({"ok": False, "error_class": "KAJABI_NOT_LOGGED_IN", "recommended_next_action": "Re-capture session"})
            return 0, json.dumps({"ok": True, "run_id": phase0_dir.name})
        if "kajabi_capture_interactive" in cmd_str:
            return 1, json.dumps({"ok": False, "error_class": "KAJABI_CAPTURE_INTERACTIVE_FAILED"})
        if "zane_finish_plan" in cmd_str:
            return 0, json.dumps({"ok": True, "run_id": finish_dir.name})
        return 1, "{}"

    def mock_run_session_check(*a):
        return 0, json.dumps({"ok": True})

    spec.loader.exec_module(mod)
    mod.STORAGE_STATE_PATH = storage
    mod.EXIT_NODE_CONFIG = tmp_path / "nonexistent_exit_node.txt"
    mod._repo_root = lambda: root
    mod._run = mock_run
    mod._run_with_exit_node = lambda c, t: mock_run(c, t)
    mod._run_session_check = mock_run_session_check
    mod._run_self_heal = lambda *a, **k: None
    mod._run_doctor_for_framebuffer = lambda *a: (True, "artifacts/novnc_debug/auto_finish_run")

    with patch.dict(os.environ, {"SOMA_KAJABI_REAUTH_POLL_TIMEOUT": "10"}):
        rc = mod.main()

    assert rc == 0
    out_dir = next((root / "artifacts" / "soma_kajabi" / "auto_finish").iterdir())
    assert (out_dir / "WAITING_FOR_HUMAN.json").exists()
    wfh = json.loads((out_dir / "WAITING_FOR_HUMAN.json").read_text())
    assert wfh["novnc_url"]
    assert ":6080" in wfh["novnc_url"]


def test_reauth_timeout_emits_artifact_bundle(tmp_path):
    """When session_check timeout occurs -> KAJABI_REAUTH_TIMEOUT + reauth_timeout_bundle.json emitted."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "config").mkdir()
    (root / "artifacts" / "soma_kajabi" / "phase0").mkdir(parents=True)
    (root / "artifacts" / "soma_kajabi" / "zane_finish_plan").mkdir(parents=True)
    (root / "artifacts" / "soma_kajabi" / "auto_finish").mkdir(parents=True)
    _setup_novnc_mock(root)

    phase0_dir = root / "artifacts" / "soma_kajabi" / "phase0" / "phase0_20250101T120000Z_abc12345"
    phase0_dir.mkdir()
    (phase0_dir / "kajabi_library_snapshot.json").write_text(json.dumps({"home": {}, "practitioner": {}}))
    storage = tmp_path / "storage.json"
    storage.write_text('{"cookies":[]}')

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
            return 1, json.dumps({"ok": False, "error_class": "KAJABI_CLOUDFLARE_BLOCKED"})
        if "kajabi_capture_interactive" in cmd_str:
            return 1, json.dumps({"ok": False})
        return 1, "{}"

    def mock_run_session_check(*a):
        return 1, json.dumps({"ok": False, "error_class": "SESSION_CHECK_TIMEOUT"})

    spec.loader.exec_module(mod)
    mod.STORAGE_STATE_PATH = storage
    mod.EXIT_NODE_CONFIG = tmp_path / "nonexistent_exit_node.txt"
    mod._repo_root = lambda: root
    mod._run = mock_run
    mod._run_with_exit_node = lambda c, t: mock_run(c, t)
    mod._run_session_check = mock_run_session_check
    mod._run_self_heal = lambda *a, **k: None
    mod._run_doctor_for_framebuffer = lambda *a: (True, "artifacts/novnc_debug/auto_finish_run")

    with patch.dict(os.environ, {"SOMA_KAJABI_REAUTH_POLL_TIMEOUT": "2"}):
        rc = mod.main()

    assert rc == 1
    out_dir = next((root / "artifacts" / "soma_kajabi" / "auto_finish").iterdir())
    assert (out_dir / "reauth_timeout_bundle.json").exists()
    bundle = json.loads((out_dir / "reauth_timeout_bundle.json").read_text())
    assert bundle["error_class"] == "KAJABI_REAUTH_TIMEOUT"
    summary = json.loads((out_dir / "SUMMARY.json").read_text())
    assert summary["error_class"] == "KAJABI_REAUTH_TIMEOUT"
