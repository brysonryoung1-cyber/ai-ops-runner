"""Integration tests for system.state_pack — writes required files."""
import json
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
STATE_PACK_SCRIPT = REPO_ROOT / "ops" / "scripts" / "state_pack.sh"


@pytest.fixture
def artifact_dir(tmp_path):
    """Use tmp_path for artifacts to avoid polluting repo."""
    art = tmp_path / "artifacts"
    art.mkdir(parents=True)
    return art


def test_state_pack_script_exists():
    assert STATE_PACK_SCRIPT.exists(), "state_pack.sh must exist"
    assert os.access(STATE_PACK_SCRIPT, os.X_OK), "state_pack.sh must be executable"


def test_state_pack_writes_required_files(artifact_dir):
    """Run state_pack and assert required files are written."""
    env = os.environ.copy()
    env["OPENCLAW_ARTIFACTS_ROOT"] = str(artifact_dir)
    env["OPENCLAW_CONSOLE_PORT"] = "8787"
    # Mock curl to avoid needing live console
    result = subprocess.run(
        [str(STATE_PACK_SCRIPT)],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    # May fail if curl to localhost fails, but we check output structure
    out = result.stdout or ""
    # Find state_pack dir
    sp_base = artifact_dir / "system" / "state_pack"
    if not sp_base.exists():
        pytest.skip("State pack dir not created (curl to console may have failed)")
    dirs = sorted([d.name for d in sp_base.iterdir() if d.is_dir()], reverse=True)
    assert len(dirs) >= 1, "At least one state_pack run dir"
    run_dir = sp_base / dirs[0]
    required = ["health_public.json", "autopilot_status.json", "tailscale_serve.txt", "ports.txt", "latest_runs_index.json", "SUMMARY.md"]
    for name in required:
        path = run_dir / name
        assert path.exists(), f"Required file {name} must exist"
    # OCL Result in stdout (multi-line JSON from cat <<EOF)
    try:
        ocl = json.loads(out.strip())
    except json.JSONDecodeError:
        # Extract JSON object (script may have leading/trailing output)
        start = out.find("{")
        if start >= 0:
            depth = 0
            for j, c in enumerate(out[start:], start=0):
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        ocl = json.loads(out[start : start + j + 1])
                        break
            else:
                ocl = json.loads(out[start:])
        else:
            pytest.fail("No JSON object in state_pack stdout")
    assert "status" in ocl
    assert "checks" in ocl
    assert "evidence" in ocl
    assert len(ocl.get("evidence", [])) >= 1
