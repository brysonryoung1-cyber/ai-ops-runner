from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
AUTOPILOT_TICK = REPO_ROOT / "ops" / "autopilot_tick.sh"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def test_git_fetch_failure_uses_health_public_fallback_and_exits_zero(tmp_path: Path) -> None:
    if shutil.which("flock") is None:
        pytest.skip("flock is required for autopilot_tick.sh")

    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "enabled").write_text("", encoding="utf-8")
    (state_dir / "fail_count.txt").write_text("0\n", encoding="utf-8")
    (state_dir / "last_deployed_sha.txt").write_text("fallback123\n", encoding="utf-8")
    (state_dir / "last_good_sha.txt").write_text("fallback123\n", encoding="utf-8")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    git_args_file = tmp_path / "git_args.txt"

    _write_executable(
        bin_dir / "git",
        f"""#!/usr/bin/env bash
echo "$*" >> "{git_args_file}"
echo "fatal: detected dubious ownership in repository at '/opt/ai-ops-runner'" >&2
exit 1
""",
    )
    _write_executable(
        bin_dir / "curl",
        """#!/usr/bin/env bash
cat <<'EOF'
{"ok": true, "build_sha": "fallback123"}
EOF
exit 0
""",
    )

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    env["OPENCLAW_AUTOPILOT_STATE_DIR"] = str(state_dir)
    env["OPENCLAW_AUTOPILOT_LOG"] = str(tmp_path / "autopilot.log")
    env["OPENCLAW_AUTOPILOT_HEALTH_URL"] = "http://127.0.0.1:8788/api/ui/health_public"

    proc = subprocess.run(
        ["bash", str(AUTOPILOT_TICK)],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    assert "fallback build_sha=fallback123" in combined

    status_path = state_dir / "last_run.json"
    assert status_path.is_file()
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["overall"] == "SKIP"
    assert status["error_class"] == "git_fetch_failed_fallback"

    git_args = git_args_file.read_text(encoding="utf-8")
    assert "safe.directory=/opt/ai-ops-runner" in git_args
