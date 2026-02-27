"""Unit tests for invariants evaluation (mocked)."""
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys_path = list(__import__("sys").path)
if str(REPO_ROOT) not in sys_path:
    __import__("sys").path.insert(0, str(REPO_ROOT))

from ops.scripts.invariants_eval import evaluate_invariants


@pytest.fixture
def mock_state_pack(tmp_path):
    """Create a minimal state pack dir for testing."""
    (tmp_path / "health_public.json").write_text(
        json.dumps({"ok": True, "build_sha": "abc1234"})
    )
    (tmp_path / "autopilot_status.json").write_text(
        json.dumps({"ok": True})
    )
    (tmp_path / "tailscale_serve.txt").write_text(
        "https://* -> http://127.0.0.1:8788"
    )
    (tmp_path / "tailscale_serve.json").write_text(
        json.dumps({"TCP": {"443": {"Handlers": {"": "http://127.0.0.1:8788"}}}})
    )
    (tmp_path / "ports.txt").write_text("LISTEN 127.0.0.1:8788")
    return tmp_path


def test_invariants_eval_structure(mock_state_pack):
    """evaluate_invariants returns expected structure."""
    from subprocess import CompletedProcess
    with patch("ops.scripts.invariants_eval._curl_http", return_value=(200, {"ok": True})):
        with patch("ops.scripts.invariants_eval.subprocess.run", return_value=CompletedProcess(args=[], returncode=1, stdout=b'{"all_ok":false}', stderr=b"")):
            result = evaluate_invariants(mock_state_pack)
    assert "invariants" in result
    assert "all_pass" in result
    assert "evidence_pointers" in result
    ids = [i["id"] for i in result["invariants"]]
    assert "hq_health_build_sha_not_unknown" in ids
    assert "autopilot_status_http_200" in ids
    assert "serve_single_root_targets_frontdoor" in ids
    assert "frontdoor_listening_8788" in ids
    assert "novnc_http_200" in ids
    assert "ws_probe_websockify_ge_10s" in ids
    assert "ws_probe_novnc_websockify_ge_10s" in ids


def test_invariants_fail_unknown_build_sha(tmp_path):
    """build_sha unknown -> hq_health_build_sha_not_unknown fails."""
    (tmp_path / "health_public.json").write_text(
        json.dumps({"ok": True, "build_sha": "unknown"})
    )
    (tmp_path / "autopilot_status.json").write_text(json.dumps({"ok": True}))
    (tmp_path / "tailscale_serve.txt").write_text("-> http://127.0.0.1:8788")
    (tmp_path / "ports.txt").write_text("8788")
    from subprocess import CompletedProcess
    with patch("ops.scripts.invariants_eval._curl_http", return_value=(0, None)):
        with patch("ops.scripts.invariants_eval.subprocess.run", return_value=CompletedProcess(args=[], returncode=1, stdout=b"{}", stderr=b"")):
            result = evaluate_invariants(tmp_path)
    hp_inv = next(i for i in result["invariants"] if i["id"] == "hq_health_build_sha_not_unknown")
    assert hp_inv["pass"] is False
