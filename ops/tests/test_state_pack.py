import json
import os
import subprocess
from pathlib import Path

import pytest
from ops.lib.state_pack_contract import (
    COMPLETION_MARKER_NAME,
    SCHEMA_VERSION,
    evaluate_state_pack_integrity,
)

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
    prune_report = artifact_dir / "system" / "state_pack" / "PRUNE_LAST.json"

    assert run_dir.exists()
    assert result_path.exists()
    assert latest_path.exists()
    assert (run_dir / COMPLETION_MARKER_NAME).exists()
    assert (run_dir / "SUMMARY.md").exists()
    assert prune_report.exists()

    result_payload = json.loads(result_path.read_text(encoding="utf-8"))
    latest_payload = json.loads(latest_path.read_text(encoding="utf-8"))

    assert result_payload["status"] == "PASS"
    assert result_payload["schema_version"] == SCHEMA_VERSION
    assert result_payload["completion_marker"] == str(run_dir / COMPLETION_MARKER_NAME)
    assert result_payload["latest_path"] == str(run_dir)
    assert latest_payload["status"] == "PASS"
    assert latest_payload["schema_version"] == SCHEMA_VERSION
    assert latest_payload["latest_path"] == str(run_dir)
    assert latest_payload["result_path"] == str(result_path)
    assert latest_payload["run_id"] == "state_pack_test_run"

    integrity = evaluate_state_pack_integrity(artifact_dir)
    assert integrity["status"] == "PASS"
    assert integrity["reason"] == "LATEST_OK"


def _write_latest_contract(artifact_dir: Path, run_dir: Path) -> Path:
    latest_path = artifact_dir / "system" / "state_pack" / "LATEST.json"
    latest_path.parent.mkdir(parents=True, exist_ok=True)
    latest_path.write_text(
        json.dumps(
            {
                "status": "PASS",
                "reason": "state_pack_generated",
                "run_id": run_dir.name,
                "generated_at": "2026-03-06T00:00:00Z",
                "finished_at": "2026-03-06T00:00:00Z",
                "latest_path": str(run_dir),
                "result_path": str(run_dir / "RESULT.json"),
                "schema_version": SCHEMA_VERSION,
                "sha": "abc1234",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return latest_path


def _write_result(run_dir: Path, status: str = "PASS") -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    for name in [
        "health_public.json",
        "autopilot_status.json",
        "llm_status.json",
        "tailscale_serve.txt",
        "ports.txt",
        "systemd_openclaw-novnc.txt",
        "systemd_openclaw-frontdoor.txt",
        "systemd_openclaw-hostd.txt",
        "systemd_openclaw-guard.txt",
        "systemd_hq.txt",
        "latest_runs_index.json",
        "build_sha.txt",
        "novnc_http_check.json",
        "ws_probe.json",
        "SUMMARY.md",
    ]:
        (run_dir / name).write_text("{}\n", encoding="utf-8")
    (run_dir / "RESULT.json").write_text(
        json.dumps({"status": status, "reason": "state_pack_generated"}) + "\n",
        encoding="utf-8",
    )
    (run_dir / COMPLETION_MARKER_NAME).write_text('{"ok":true}\n', encoding="utf-8")


def test_validate_latest_fails_when_latest_missing(artifact_dir: Path) -> None:
    payload = evaluate_state_pack_integrity(artifact_dir)
    assert payload["status"] == "FAIL"
    assert payload["reason"] == "LATEST_MISSING"


def test_validate_latest_fails_when_latest_json_invalid(artifact_dir: Path) -> None:
    latest_path = artifact_dir / "system" / "state_pack" / "LATEST.json"
    latest_path.parent.mkdir(parents=True, exist_ok=True)
    latest_path.write_text("{not-json}\n", encoding="utf-8")

    payload = evaluate_state_pack_integrity(artifact_dir)
    assert payload["status"] == "FAIL"
    assert payload["reason"] == "LATEST_JSON_INVALID"


def test_validate_latest_fails_when_schema_mismatches(artifact_dir: Path) -> None:
    run_dir = artifact_dir / "system" / "state_pack" / "schema_mismatch"
    _write_result(run_dir)
    latest_path = _write_latest_contract(artifact_dir, run_dir)
    latest_payload = json.loads(latest_path.read_text(encoding="utf-8"))
    latest_payload["schema_version"] = 999
    latest_path.write_text(json.dumps(latest_payload) + "\n", encoding="utf-8")

    payload = evaluate_state_pack_integrity(artifact_dir)
    assert payload["status"] == "FAIL"
    assert payload["reason"] == "LATEST_SCHEMA_MISMATCH"


def test_validate_latest_fails_when_latest_path_missing(artifact_dir: Path) -> None:
    run_dir = artifact_dir / "system" / "state_pack" / "missing_run"
    _write_latest_contract(artifact_dir, run_dir)

    payload = evaluate_state_pack_integrity(artifact_dir)
    assert payload["status"] == "FAIL"
    assert payload["reason"] == "LATEST_PATH_MISSING"


def test_validate_latest_fails_when_completion_marker_missing(artifact_dir: Path) -> None:
    run_dir = artifact_dir / "system" / "state_pack" / "no_marker"
    _write_result(run_dir)
    (run_dir / COMPLETION_MARKER_NAME).unlink()
    _write_latest_contract(artifact_dir, run_dir)

    payload = evaluate_state_pack_integrity(artifact_dir)
    assert payload["status"] == "FAIL"
    assert payload["reason"] == "PACK_INCOMPLETE"


def test_validate_latest_fails_when_result_missing(artifact_dir: Path) -> None:
    run_dir = artifact_dir / "system" / "state_pack" / "no_result"
    _write_result(run_dir)
    (run_dir / "RESULT.json").unlink()
    _write_latest_contract(artifact_dir, run_dir)

    payload = evaluate_state_pack_integrity(artifact_dir)
    assert payload["status"] == "FAIL"
    assert payload["reason"] == "RESULT_JSON_MISSING"


def test_validate_latest_fails_when_result_not_pass(artifact_dir: Path) -> None:
    run_dir = artifact_dir / "system" / "state_pack" / "result_fail"
    _write_result(run_dir, status="FAIL")
    _write_latest_contract(artifact_dir, run_dir)

    payload = evaluate_state_pack_integrity(artifact_dir)
    assert payload["status"] == "FAIL"
    assert payload["reason"] == "RESULT_NOT_PASS"
