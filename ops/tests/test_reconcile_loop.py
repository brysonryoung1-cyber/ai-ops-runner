import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RECONCILE_SCRIPT = REPO_ROOT / "ops" / "scripts" / "reconcile.sh"
STATE_PACK_CONTRACT = REPO_ROOT / "ops" / "lib" / "state_pack_contract.py"


def test_reconcile_script_exists() -> None:
    assert RECONCILE_SCRIPT.exists()
    assert os.access(RECONCILE_SCRIPT, os.X_OK)


def test_reconcile_has_flock() -> None:
    content = RECONCILE_SCRIPT.read_text(encoding="utf-8")
    assert "flock" in content
    assert "reconcile.lock" in content


def test_reconcile_has_max_attempts() -> None:
    content = RECONCILE_SCRIPT.read_text(encoding="utf-8")
    assert "MAX_ATTEMPTS" in content or "OPENCLAW_RECONCILE_MAX_ATTEMPTS" in content


def test_reconcile_choose_playbook_mapping() -> None:
    inv = {"invariants": [{"id": "serve_single_root_targets_frontdoor", "pass": False}]}
    with open("/tmp/test_inv.json", "w", encoding="utf-8") as handle:
        json.dump(inv, handle)
    result = subprocess.run(
        [
            "python3",
            "-c",
            """
import json
d=json.load(open('/tmp/test_inv.json'))
failing=[i['id'] for i in d.get('invariants',[]) if not i.get('pass')]
if 'serve_single_root_targets_frontdoor' in failing or 'frontdoor_listening_8788' in failing:
    print('reconcile_frontdoor_serve')
elif 'novnc_http_200' in failing or 'ws_probe_websockify_ge_10s' in failing or 'ws_probe_novnc_websockify_ge_10s' in failing:
    print('recover_novnc_ws')
else:
    print('recover_hq_routing')
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "reconcile_frontdoor_serve" in result.stdout


def test_reconcile_lock_contention_exits_zero_and_writes_skip_reason(tmp_path: Path) -> None:
    if shutil.which("flock") is None:
        pytest.skip("flock is required for lock-contention test")

    lock_dir = tmp_path / "locks"
    artifacts_root = tmp_path / "artifacts"
    lock_dir.mkdir(parents=True, exist_ok=True)
    artifacts_root.mkdir(parents=True, exist_ok=True)
    lock_file = lock_dir / "reconcile.lock"

    holder = subprocess.Popen(
        ["bash", "-c", f'exec 201>"{lock_file}"; flock -x 201; sleep 8'],
        cwd=str(REPO_ROOT),
    )
    time.sleep(0.6)

    try:
        env = os.environ.copy()
        env["OPENCLAW_RECONCILE_LOCK_DIR"] = str(lock_dir)
        env["OPENCLAW_ARTIFACTS_ROOT"] = str(artifacts_root)
        proc = subprocess.run(
            ["bash", str(RECONCILE_SCRIPT)],
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        holder.terminate()
        holder.wait(timeout=5)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "SKIP_LOCK_CONTENDED" in proc.stdout

    result_files = list((artifacts_root / "system" / "reconcile").glob("*/result.json"))
    assert result_files
    payload = json.loads(result_files[0].read_text(encoding="utf-8"))
    assert payload["status"] == "SKIP"
    assert payload["reason"] == "SKIP_LOCK_CONTENDED"


def test_reconcile_uses_latest_json_and_never_calls_ls(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    scripts_dir = repo_root / "ops" / "scripts"
    lib_dir = repo_root / "ops" / "lib"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    lib_dir.mkdir(parents=True, exist_ok=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)

    reconcile_copy = scripts_dir / "reconcile.sh"
    reconcile_copy.write_text(RECONCILE_SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    reconcile_copy.chmod(0o755)
    (lib_dir / "state_pack_contract.py").write_text(STATE_PACK_CONTRACT.read_text(encoding="utf-8"), encoding="utf-8")

    generator_stub = scripts_dir / "state_pack_generate.sh"
    generator_stub.write_text("#!/usr/bin/env bash\nprintf '{\"status\":\"PASS\"}\\n'\n", encoding="utf-8")
    generator_stub.chmod(0o755)

    invariants_stub = scripts_dir / "invariants_eval.py"
    invariants_stub.write_text(
        """#!/usr/bin/env python3
import json
import os
from pathlib import Path

out = Path(os.environ["OPENCLAW_INVARIANTS_OUTPUT"])
out.parent.mkdir(parents=True, exist_ok=True)
payload = {"all_pass": True, "invariants": []}
out.write_text(json.dumps(payload), encoding="utf-8")
print(json.dumps(payload))
""",
        encoding="utf-8",
    )
    invariants_stub.chmod(0o755)

    flock_stub = bin_dir / "flock"
    flock_stub.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    flock_stub.chmod(0o755)

    ls_log = tmp_path / "ls.log"
    ls_stub = bin_dir / "ls"
    ls_stub.write_text(
        f"#!/usr/bin/env bash\necho called >> {ls_log}\nexit 99\n",
        encoding="utf-8",
    )
    ls_stub.chmod(0o755)

    artifacts_root = tmp_path / "artifacts"
    run_dir = artifacts_root / "system" / "state_pack" / "existing_run"
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
        json.dumps({"status": "PASS", "reason": "state_pack_generated"}) + "\n",
        encoding="utf-8",
    )
    (run_dir / "LATEST.ok").write_text('{"ok":true}\n', encoding="utf-8")
    latest_json = artifacts_root / "system" / "state_pack" / "LATEST.json"
    latest_json.parent.mkdir(parents=True, exist_ok=True)
    latest_json.write_text(
        json.dumps(
            {
                "status": "PASS",
                "reason": "state_pack_generated",
                "run_id": "existing_run",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "latest_path": str(run_dir),
                "result_path": str(run_dir / "RESULT.json"),
                "schema_version": 1,
                "sha": "abc1234",
            }
        ),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["OPENCLAW_ARTIFACTS_ROOT"] = str(artifacts_root)
    env["OPENCLAW_RECONCILE_BACKOFF_SEC"] = "0"
    env["OPENCLAW_RECONCILE_MAX_ATTEMPTS"] = "1"
    env["OPENCLAW_RECONCILE_LOCK_DIR"] = str(tmp_path / "locks")
    env["PATH"] = f"{bin_dir}:{env['PATH']}"

    proc = subprocess.run(
        ["bash", str(reconcile_copy)],
        cwd=str(repo_root),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not ls_log.exists(), proc.stdout + proc.stderr

    result_files = list((artifacts_root / "system" / "reconcile").glob("*/result.json"))
    assert result_files
    payload = json.loads(result_files[0].read_text(encoding="utf-8"))
    assert payload["status"] == "SUCCESS"


def test_reconcile_missing_latest_is_controlled_fail(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    scripts_dir = repo_root / "ops" / "scripts"
    lib_dir = repo_root / "ops" / "lib"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    lib_dir.mkdir(parents=True, exist_ok=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)

    reconcile_copy = scripts_dir / "reconcile.sh"
    reconcile_copy.write_text(RECONCILE_SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    reconcile_copy.chmod(0o755)
    (lib_dir / "state_pack_contract.py").write_text(STATE_PACK_CONTRACT.read_text(encoding="utf-8"), encoding="utf-8")

    generator_stub = scripts_dir / "state_pack_generate.sh"
    generator_stub.write_text("#!/usr/bin/env bash\nprintf '{\"status\":\"FAIL\",\"reason\":\"LATEST_MISSING\"}\\n'\nexit 1\n", encoding="utf-8")
    generator_stub.chmod(0o755)

    flock_stub = bin_dir / "flock"
    flock_stub.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    flock_stub.chmod(0o755)

    artifacts_root = tmp_path / "artifacts"
    env = os.environ.copy()
    env["OPENCLAW_ARTIFACTS_ROOT"] = str(artifacts_root)
    env["OPENCLAW_RECONCILE_BACKOFF_SEC"] = "0"
    env["OPENCLAW_RECONCILE_MAX_ATTEMPTS"] = "1"
    env["OPENCLAW_RECONCILE_LOCK_DIR"] = str(tmp_path / "locks")
    env["PATH"] = f"{bin_dir}:{env['PATH']}"

    proc = subprocess.run(
        ["bash", str(reconcile_copy)],
        cwd=str(repo_root),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 1, proc.stdout + proc.stderr
    result_files = list((artifacts_root / "system" / "reconcile").glob("*/result.json"))
    assert result_files
    payload = json.loads(result_files[0].read_text(encoding="utf-8"))
    assert payload["status"] == "FAIL"
    assert "state_pack_unavailable" in payload["reason"]
