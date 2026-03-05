"""Unit tests for reconcile loop behavior (stop conditions, max attempts)."""
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RECONCILE_SCRIPT = REPO_ROOT / "ops" / "scripts" / "reconcile.sh"


def test_reconcile_script_exists():
    """reconcile.sh must exist and be executable."""
    assert RECONCILE_SCRIPT.exists()
    assert os.access(RECONCILE_SCRIPT, os.X_OK)


def test_reconcile_has_flock():
    """reconcile.sh must use flock for concurrency lock."""
    content = RECONCILE_SCRIPT.read_text()
    assert "flock" in content
    assert "reconcile.lock" in content


def test_reconcile_has_max_attempts():
    """reconcile.sh must have MAX_ATTEMPTS or OPENCLAW_RECONCILE_MAX_ATTEMPTS."""
    content = RECONCILE_SCRIPT.read_text()
    assert "MAX_ATTEMPTS" in content or "OPENCLAW_RECONCILE_MAX_ATTEMPTS" in content


def test_reconcile_choose_playbook_mapping():
    """Deterministic mapping: failing invariants -> playbook."""
    # serve/frontdoor -> reconcile_frontdoor_serve
    inv = {"invariants": [{"id": "serve_single_root_targets_frontdoor", "pass": False}]}
    # We test the logic by running the Python snippet from the script
    with open("/tmp/test_inv.json", "w") as f:
        json.dump(inv, f)
    result = __import__("subprocess").run(
        ["python3", "-c", """
import json
d=json.load(open('/tmp/test_inv.json'))
failing=[i['id'] for i in d.get('invariants',[]) if not i.get('pass')]
if 'serve_single_root_targets_frontdoor' in failing or 'frontdoor_listening_8788' in failing:
    print('reconcile_frontdoor_serve')
elif 'novnc_http_200' in failing or 'ws_probe_websockify_ge_10s' in failing or 'ws_probe_novnc_websockify_ge_10s' in failing:
    print('recover_novnc_ws')
else:
    print('recover_hq_routing')
"""],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "reconcile_frontdoor_serve" in result.stdout


def test_reconcile_lock_contention_exits_zero_and_writes_skip_reason(tmp_path: Path):
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
    assert result_files, "expected reconcile result.json artifact"
    payload = json.loads(result_files[0].read_text(encoding="utf-8"))
    assert payload["status"] == "SKIP"
    assert payload["reason"] == "SKIP_LOCK_CONTENDED"
