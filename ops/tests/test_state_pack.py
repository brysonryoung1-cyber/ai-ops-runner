import json
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
STATE_PACK_SCRIPT = REPO_ROOT / "ops" / "scripts" / "state_pack.sh"
STATE_PACK_GENERATOR = REPO_ROOT / "ops" / "scripts" / "state_pack_generate.sh"


@pytest.fixture
def artifact_dir(tmp_path: Path) -> Path:
    art = tmp_path / "artifacts"
    art.mkdir(parents=True)
    return art


@pytest.fixture
def stub_bin(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)

    curl_stub = bin_dir / "curl"
    curl_stub.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
args="$*"
last="${@: -1}"
if [[ "$args" == *"%{http_code}"* ]]; then
  printf '200'
  exit 0
fi
case "$last" in
  *"/api/ui/health_public")
    printf '{"ok":true,"build_sha":"abc1234"}'
    ;;
  *"/api/autopilot/status")
    printf '{"ok":true}'
    ;;
  *"/api/llm/status")
    printf '{"ok":true,"providers":[]}'
    ;;
  *"/api/ui/version")
    printf '{"drift_status":"clean","drift":false}'
    ;;
  *)
    printf '{}'
    ;;
esac
""",
        encoding="utf-8",
    )
    curl_stub.chmod(0o755)

    tailscale_stub = bin_dir / "tailscale"
    tailscale_stub.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
if [ "${1:-}" = "serve" ] && [ "${2:-}" = "status" ] && [ "${3:-}" = "--json" ]; then
  printf '{"TCP":{"443":{"HTTPS":true,"Target":"http://127.0.0.1:8788"}}}'
  exit 0
fi
if [ "${1:-}" = "serve" ] && [ "${2:-}" = "status" ]; then
  printf 'https://443 -> http://127.0.0.1:8788\\n'
  exit 0
fi
printf 'ok\\n'
""",
        encoding="utf-8",
    )
    tailscale_stub.chmod(0o755)

    systemctl_stub = bin_dir / "systemctl"
    systemctl_stub.write_text("#!/usr/bin/env bash\nprintf 'active\\n'\n", encoding="utf-8")
    systemctl_stub.chmod(0o755)

    journalctl_stub = bin_dir / "journalctl"
    journalctl_stub.write_text("#!/usr/bin/env bash\nprintf 'journal\\n'\n", encoding="utf-8")
    journalctl_stub.chmod(0o755)

    flock_stub = bin_dir / "flock"
    flock_stub.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    flock_stub.chmod(0o755)

    ss_stub = bin_dir / "ss"
    ss_stub.write_text(
        "#!/usr/bin/env bash\nprintf 'LISTEN 0 128 127.0.0.1:8787 0.0.0.0:*\\nLISTEN 0 128 127.0.0.1:8788 0.0.0.0:*\\nLISTEN 0 128 127.0.0.1:6080 0.0.0.0:*\\n'\n",
        encoding="utf-8",
    )
    ss_stub.chmod(0o755)
    return bin_dir


def test_state_pack_scripts_exist() -> None:
    assert STATE_PACK_SCRIPT.exists()
    assert STATE_PACK_GENERATOR.exists()
    assert os.access(STATE_PACK_SCRIPT, os.X_OK)
    assert os.access(STATE_PACK_GENERATOR, os.X_OK)


def test_state_pack_generator_writes_result_and_latest(artifact_dir: Path, stub_bin: Path) -> None:
    env = os.environ.copy()
    env["OPENCLAW_ARTIFACTS_ROOT"] = str(artifact_dir)
    env["OPENCLAW_RUN_ID"] = "state_pack_test_run"
    env["PATH"] = f"{stub_bin}:{env['PATH']}"

    proc = subprocess.run(
        [str(STATE_PACK_GENERATOR)],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    stdout_payload = json.loads(proc.stdout.strip())
    assert stdout_payload["status"] == "PASS"
    assert stdout_payload["reason"] == "state_pack_generated"

    run_dir = artifact_dir / "system" / "state_pack" / "state_pack_test_run"
    result_path = run_dir / "RESULT.json"
    latest_path = artifact_dir / "system" / "state_pack" / "LATEST.json"

    assert run_dir.exists()
    assert result_path.exists()
    assert latest_path.exists()
    assert (run_dir / "SUMMARY.md").exists()

    result_payload = json.loads(result_path.read_text(encoding="utf-8"))
    latest_payload = json.loads(latest_path.read_text(encoding="utf-8"))

    assert result_payload["status"] == "PASS"
    assert result_payload["latest_path"] == str(run_dir)
    assert latest_payload["status"] == "PASS"
    assert latest_payload["latest_path"] == str(run_dir)
    assert latest_payload["result_path"] == str(result_path)
    assert latest_payload["run_id"] == "state_pack_test_run"
