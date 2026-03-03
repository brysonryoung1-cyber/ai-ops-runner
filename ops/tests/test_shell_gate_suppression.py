"""Test that shell scripts exit 0 (suppressed) when human gate is active and OPENCLAW_FORCE_AUTORECOVER!=1."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

GATED_SCRIPTS = [
    "ops/scripts/novnc_shm_guard.sh",
    "ops/scripts/novnc_shm_fix.sh",
    "ops/scripts/openclaw_novnc_routing_fix.sh",
    "ops/scripts/novnc_restart.sh",
]


@pytest.fixture()
def gate_env(tmp_path: Path):
    """Create a temporary gate state dir with an active gate file."""
    gate_dir = tmp_path / "state" / "human_gate"
    gate_dir.mkdir(parents=True)
    expires = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    gate = {
        "active": True,
        "project_id": "soma_kajabi",
        "run_id": "test_run",
        "novnc_url": "https://test.example.com",
        "reason": "test",
        "expires_at": expires,
    }
    (gate_dir / "soma_kajabi.json").write_text(json.dumps(gate))
    env = os.environ.copy()
    env["OPENCLAW_STATE_ROOT"] = str(tmp_path / "state")
    env.pop("OPENCLAW_FORCE_AUTORECOVER", None)
    return env


@pytest.mark.parametrize("script_rel", GATED_SCRIPTS)
def test_gate_active_no_force_exits_0(gate_env, script_rel):
    """With gate active and no force override, gated script exits 0 (suppressed)."""
    script = REPO_ROOT / script_rel
    if not script.exists():
        pytest.skip(f"{script_rel} not found")
    r = subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        timeout=10,
        env=gate_env,
    )
    assert r.returncode == 0, f"Expected exit 0 (suppressed) but got {r.returncode}: {r.stderr[:300]}"
    combined = (r.stdout + r.stderr).lower()
    assert "suppressed" in combined or "human gate active" in combined, \
        f"Expected suppression message in output: {(r.stdout + r.stderr)[:300]}"
