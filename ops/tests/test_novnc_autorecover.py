"""Hermetic tests for novnc_autorecover — mock all subprocess calls."""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# ── Helpers ──

def _make_doctor_output(ok: bool, url: str = "", error_class: str = "") -> str:
    doc = {"ok": ok, "novnc_url": url}
    if error_class:
        doc["error_class"] = error_class
    return json.dumps(doc) + "\n"


def _completed_process(returncode: int, stdout: str = "", stderr: str = ""):
    """Create a mock CompletedProcess."""
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr,
    )


class _MockSubprocessRunner:
    """Track and replay subprocess.run calls for novnc_autorecover."""

    def __init__(self, scenario: str):
        self.scenario = scenario
        self.calls: list[list[str]] = []
        self._doctor_call_count = 0
        self.fixpack_dir: str | None = None

    def run(self, cmd, **kwargs):
        self.calls.append(list(str(c) for c in cmd))
        # Match on the primary command (first two args) to avoid false matches from pointer args
        primary = " ".join(str(c) for c in cmd[:3])

        if "novnc_fixpack_emit" in primary:
            # Must check BEFORE journalctl since pointer args may contain "journalctl"
            pass  # handled below
        elif "openclaw_novnc_doctor.sh" in primary:
            self._doctor_call_count += 1
            return self._handle_doctor(primary)
        elif "novnc_shm_fix.sh" in primary:
            return _completed_process(0, "shm fixed")
        elif "openclaw_novnc_routing_fix.sh" in primary:
            return _completed_process(0, "routing fixed")
        elif cmd[0] == "systemctl" and "restart" in primary:
            return _completed_process(0, "")
        elif cmd[0] == "journalctl":
            journal_text = ""
            if self.scenario == "shm_fail_then_pass":
                journal_text = "shmget: No space left on device"
            return _completed_process(0, journal_text)
        if "novnc_fixpack_emit" in primary:
            # cmd = ["bash", script, triage_dir, error_class, ...]
            triage_dir = str(cmd[2]) if len(cmd) > 2 else "/tmp"
            self.fixpack_dir = triage_dir
            p = Path(triage_dir)
            p.mkdir(parents=True, exist_ok=True)
            (p / "triage.json").write_text('{"error_class":"mocked"}')
            (p / "evidence_bundle.json").write_text('{"mocked":true}')
            (p / "CSR_PROMPT.txt").write_text("MODE: IMPLEMENTER (Opus)\nmocked")
            return _completed_process(0, triage_dir)
        return _completed_process(0, "")

    def _handle_doctor(self, cmd_str: str) -> subprocess.CompletedProcess:
        if self.scenario == "pass_immediately":
            return _completed_process(0, _make_doctor_output(True, "https://test/novnc"))
        if self.scenario == "fail_then_pass":
            if self._doctor_call_count <= 1:
                return _completed_process(1, _make_doctor_output(False, "", "NOVNC_NOT_READY"))
            return _completed_process(0, _make_doctor_output(True, "https://test/novnc"))
        if self.scenario == "shm_fail_then_pass":
            if self._doctor_call_count <= 1:
                return _completed_process(1, _make_doctor_output(False, "", "service_not_active"))
            return _completed_process(0, _make_doctor_output(True, "https://test/novnc"))
        if self.scenario == "always_fail":
            return _completed_process(1, _make_doctor_output(False, "", "NOVNC_NOT_READY"))
        return _completed_process(1, _make_doctor_output(False, "", "NOVNC_NOT_READY"))


def _setup_fake_root(tmp_path: Path) -> Path:
    """Create a fake repo root with required script stubs."""
    fake_root = tmp_path
    (fake_root / "config" / "project_state.json").parent.mkdir(parents=True, exist_ok=True)
    (fake_root / "config" / "project_state.json").write_text("{}")
    (fake_root / "ops" / "openclaw_novnc_doctor.sh").parent.mkdir(parents=True, exist_ok=True)
    (fake_root / "ops" / "openclaw_novnc_doctor.sh").touch()
    (fake_root / "ops" / "openclaw_novnc_doctor.sh").chmod(0o755)
    (fake_root / "ops" / "scripts" / "novnc_shm_fix.sh").parent.mkdir(parents=True, exist_ok=True)
    (fake_root / "ops" / "scripts" / "novnc_shm_fix.sh").touch()
    (fake_root / "ops" / "scripts" / "openclaw_novnc_routing_fix.sh").touch()
    (fake_root / "ops" / "scripts" / "novnc_fixpack_emit.sh").touch()
    return fake_root


def _load_autorecover_module():
    """Load novnc_autorecover as a fresh module."""
    mod_name = f"novnc_autorecover_{id(object())}"
    spec = importlib.util.spec_from_file_location(
        mod_name, REPO_ROOT / "ops" / "scripts" / "novnc_autorecover.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_autorecover(tmp_path: Path, scenario: str) -> tuple[int, dict, list[dict], _MockSubprocessRunner]:
    """Import and run novnc_autorecover.main() with mocked subprocess.
    Returns (exit_code, result_dict, steps_list, runner)."""
    runner = _MockSubprocessRunner(scenario)
    fake_root = _setup_fake_root(tmp_path)

    with patch("subprocess.run", side_effect=runner.run):
        with patch.dict(os.environ, {"OPENCLAW_REPO_ROOT": str(fake_root), "OPENCLAW_RUN_ID": "test_run"}):
            mod = _load_autorecover_module()
            rc = mod.main()

    out_dir = fake_root / "artifacts" / "novnc_autorecover" / "test_run"
    result_path = out_dir / "autorecover_result.json"
    result = json.loads(result_path.read_text()) if result_path.exists() else {}
    steps_path = out_dir / "steps_executed.json"
    steps = json.loads(steps_path.read_text()) if steps_path.exists() else []
    return rc, result, steps, runner


# ── Tests ──

def test_pass_without_remediation(tmp_path):
    """Doctor PASS on first check → no remediation steps, exit 0."""
    rc, result, steps, _runner = _run_autorecover(tmp_path, "pass_immediately")
    assert rc == 0
    assert result["status"] == "PASS"
    assert result["novnc_url"] == "https://test/novnc"
    assert len(steps) == 1
    assert steps[0]["step"] == "doctor_fast_1"
    assert steps[0]["exit_code"] == 0


def test_fail_remediate_pass(tmp_path):
    """Doctor FAIL→remediate→PASS (deep doctor succeeds)."""
    rc, result, steps, _runner = _run_autorecover(tmp_path, "fail_then_pass")
    assert rc == 0
    assert result["status"] == "PASS"
    step_names = [s["step"] for s in steps]
    assert "doctor_fast_1" in step_names
    assert "restart_1" in step_names
    assert "routing_fix" in step_names


def test_shm_indicated_remediation(tmp_path):
    """Journal indicates shm issue → shm_fix step runs (not skipped)."""
    rc, result, steps, _runner = _run_autorecover(tmp_path, "shm_fail_then_pass")
    assert rc == 0
    step_names = [s["step"] for s in steps]
    shm_step = [s for s in steps if s["step"] == "shm_fix"]
    assert len(shm_step) == 1
    assert shm_step[0]["detail"] != "skipped_not_indicated"


def test_fail_remediate_fail_emits_fixpack(tmp_path):
    """Doctor FAIL→remediate→FAIL → fixpack cmd is invoked, exit 1."""
    rc, result, steps, runner = _run_autorecover(tmp_path, "always_fail")
    assert rc == 1
    assert result["status"] == "FAIL"
    step_names = [s["step"] for s in steps]
    assert "doctor_fast_1" in step_names
    assert "doctor_deep" in step_names
    assert "doctor_fast_2" in step_names
    # Verify fixpack subprocess was invoked
    fixpack_calls = [c for c in runner.calls if any("novnc_fixpack_emit" in arg for arg in c)]
    assert len(fixpack_calls) == 1, f"Expected 1 fixpack call, got {len(fixpack_calls)}"
    # Verify mock actually created files at the fixpack_dir
    assert runner.fixpack_dir is not None, "Mock fixpack handler was not reached"
    fixpack_path = Path(runner.fixpack_dir)
    assert fixpack_path.exists(), f"Fixpack dir {runner.fixpack_dir} does not exist"
    assert (fixpack_path / "triage.json").exists(), f"triage.json not found in {runner.fixpack_dir}"
    assert (fixpack_path / "evidence_bundle.json").exists()
    assert (fixpack_path / "CSR_PROMPT.txt").exists()


def test_step_order(tmp_path):
    """Verify exact step execution order per spec."""
    rc, result, steps, _runner = _run_autorecover(tmp_path, "always_fail")
    step_names = [s["step"] for s in steps]
    expected_order = [
        "doctor_fast_1",
        "shm_fix",
        "restart_1",
        "routing_fix",
        "restart_2",
        "doctor_deep",
        "doctor_fast_2",
    ]
    assert step_names == expected_order


# ── Registry / script existence tests ──

def test_autorecover_registered():
    """openclaw_novnc_autorecover is in action_registry.json."""
    registry = REPO_ROOT / "config" / "action_registry.json"
    data = json.loads(registry.read_text())
    ids = [a["id"] for a in data["actions"]]
    assert "openclaw_novnc_autorecover" in ids


def test_autorecover_in_allowlist():
    """openclaw_novnc_autorecover is in allowlist.ts."""
    allowlist = REPO_ROOT / "apps" / "openclaw-console" / "src" / "lib" / "allowlist.ts"
    content = allowlist.read_text()
    assert "openclaw_novnc_autorecover" in content


def test_autorecover_script_exists():
    """novnc_autorecover.py exists and has required content."""
    script = REPO_ROOT / "ops" / "scripts" / "novnc_autorecover.py"
    assert script.exists()
    content = script.read_text()
    assert "doctor" in content
    assert "shm_fix" in content
    assert "routing_fix" in content
    assert "fixpack" in content


def test_fixpack_emit_script_exists():
    """novnc_fixpack_emit.sh exists."""
    script = REPO_ROOT / "ops" / "scripts" / "novnc_fixpack_emit.sh"
    assert script.exists()


def test_csr_prompt_from_bundle_script_exists():
    """csr_prompt_from_bundle.sh exists."""
    script = REPO_ROOT / "ops" / "scripts" / "csr_prompt_from_bundle.sh"
    assert script.exists()
