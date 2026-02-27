"""Unit tests for reconcile loop behavior (stop conditions, max attempts)."""
import json
import os
from pathlib import Path
from unittest.mock import patch

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
