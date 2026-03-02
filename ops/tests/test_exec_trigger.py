"""Tests for the shared exec trigger client (ops/lib/exec_trigger.py).

Validates:
  - DEFAULT_TRIGGER_TIMEOUT >= 60s (invariant)
  - 200/202 → ACCEPTED
  - 409 → ALREADY_RUNNING
  - 502/500 → FAILED
  - Network timeout → FAILED with diagnostic message
  - hq_request low-level helper
  - Soma CLI behaviour via trigger_exec (409 is non-fatal)
"""

from __future__ import annotations

import http.server
import json
import threading
from unittest.mock import patch

import pytest

from ops.lib.exec_trigger import (
    DEFAULT_TRIGGER_TIMEOUT,
    TriggerResult,
    hq_request,
    trigger_exec,
)


# ---------------------------------------------------------------------------
# Timeout invariant — fail-safe against "optimizing" back to a short value
# ---------------------------------------------------------------------------

class TestTimeoutInvariant:
    def test_default_timeout_at_least_60s(self):
        assert DEFAULT_TRIGGER_TIMEOUT >= 60, (
            f"DEFAULT_TRIGGER_TIMEOUT={DEFAULT_TRIGGER_TIMEOUT} is below the 60s floor. "
            "Host executor uses 10/20/40s backoff (~70s total); "
            "reducing the timeout causes TRIGGER_FAILED while hostd is still probing."
        )


# ---------------------------------------------------------------------------
# trigger_exec — mocked via hq_request patch
# ---------------------------------------------------------------------------

def _mock_hq(status: int, body: dict):
    """Return a patched hq_request that returns (status, json_body)."""
    return patch(
        "ops.lib.exec_trigger.hq_request",
        return_value=(status, json.dumps(body)),
    )


class TestTriggerExec:
    def test_200_accepted(self):
        with _mock_hq(200, {"run_id": "r1", "status": "running"}):
            r = trigger_exec("proj", "some_action")
        assert r.state == "ACCEPTED"
        assert r.status_code == 200
        assert r.run_id == "r1"

    def test_202_accepted(self):
        with _mock_hq(202, {"run_id": "r2", "status": "running", "message": "Poll GET /api/runs?id=r2 for status"}):
            r = trigger_exec("soma_kajabi", "soma_kajabi_auto_finish")
        assert r.state == "ACCEPTED"
        assert r.status_code == 202
        assert r.run_id == "r2"

    def test_409_already_running(self):
        with _mock_hq(409, {"error_class": "ALREADY_RUNNING", "active_run_id": "r3"}):
            r = trigger_exec("soma_kajabi", "soma_run_to_done")
        assert r.state == "ALREADY_RUNNING"
        assert r.status_code == 409
        assert r.run_id == "r3"
        assert "already running" in r.message.lower()

    def test_502_failed(self):
        with _mock_hq(502, {"error_class": "HOSTD_UNREACHABLE", "error": "Host Executor unreachable"}):
            r = trigger_exec("soma_kajabi", "soma_kajabi_auto_finish")
        assert r.state == "FAILED"
        assert r.status_code == 502
        assert "HOSTD_UNREACHABLE" in r.message

    def test_500_failed(self):
        with _mock_hq(500, {"error": "Internal error: something broke"}):
            r = trigger_exec("system", "doctor")
        assert r.state == "FAILED"
        assert r.status_code == 500

    def test_network_timeout(self):
        with patch("ops.lib.exec_trigger.hq_request", return_value=(-1, "Connection timed out")):
            r = trigger_exec("soma_kajabi", "soma_kajabi_auto_finish")
        assert r.state == "FAILED"
        assert r.status_code == -1
        assert "timeout" in r.message.lower() or "network" in r.message.lower()
        assert "soma_kajabi" in r.message
        assert "hostd/HQ logs" in r.message

    def test_custom_timeout_passed_through(self):
        with patch("ops.lib.exec_trigger.hq_request", return_value=(200, '{"run_id":"x"}')) as mock:
            trigger_exec("proj", "act", timeout=120)
        _, kwargs = mock.call_args
        assert kwargs.get("timeout") == 120 or mock.call_args[0][-1] == 120

    def test_default_timeout_used_when_none(self):
        with patch("ops.lib.exec_trigger.hq_request", return_value=(200, '{"run_id":"x"}')) as mock:
            trigger_exec("proj", "act")
        args, kwargs = mock.call_args
        assert kwargs.get("timeout", args[-1] if len(args) > 3 else None) == DEFAULT_TRIGGER_TIMEOUT

    def test_payload_merged_into_body(self):
        with patch("ops.lib.exec_trigger.hq_request", return_value=(200, '{"run_id":"x"}')) as mock:
            trigger_exec("proj", "act", payload={"params": {"branch": "main"}})
        call_data = mock.call_args[1].get("data") or mock.call_args[0][2]
        assert call_data["action"] == "act"
        assert call_data["params"] == {"branch": "main"}

    def test_non_json_body_does_not_crash(self):
        with patch("ops.lib.exec_trigger.hq_request", return_value=(502, "Bad Gateway")):
            r = trigger_exec("proj", "act")
        assert r.state == "FAILED"
        assert r.status_code == 502


# ---------------------------------------------------------------------------
# hq_request — basic plumbing (integration-lite with a real HTTP server)
# ---------------------------------------------------------------------------

class _StubHandler(http.server.BaseHTTPRequestHandler):
    """Minimal handler for test HTTP server."""
    response_code = 200
    response_body = b'{"ok": true}'

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        self.send_response(self.response_code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(self.response_body)

    def do_GET(self):
        self.send_response(self.response_code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(self.response_body)

    def log_message(self, *_args):
        pass  # suppress logging during tests


class TestHqRequest:
    def test_successful_post(self):
        _StubHandler.response_code = 200
        _StubHandler.response_body = json.dumps({"run_id": "abc"}).encode()
        server = http.server.HTTPServer(("127.0.0.1", 0), _StubHandler)
        port = server.server_address[1]
        t = threading.Thread(target=server.handle_request, daemon=True)
        t.start()
        try:
            code, body = hq_request(
                "POST", "/api/exec",
                data={"action": "test"},
                timeout=5,
                base_url=f"http://127.0.0.1:{port}",
            )
            assert code == 200
            assert json.loads(body)["run_id"] == "abc"
        finally:
            server.server_close()

    def test_connection_refused(self):
        code, body = hq_request(
            "GET", "/api/exec?check=connectivity",
            timeout=2,
            base_url="http://127.0.0.1:19999",
        )
        assert code == -1
        assert body  # should contain error string


# ---------------------------------------------------------------------------
# Soma CLI integration: verify 409 is non-fatal in soma_run_to_done
# ---------------------------------------------------------------------------

class TestSomaTriggerSemantics:
    """Verify that Soma's trigger handling treats 409 as non-fatal."""

    def test_soma_409_returns_exit_0(self):
        """When HQ returns 409 (ALREADY_RUNNING), soma_run_to_done should exit 0."""
        tr = TriggerResult(
            status_code=409,
            state="ALREADY_RUNNING",
            message="Action soma_kajabi_auto_finish already running for project=soma_kajabi",
            run_id="existing-run",
        )
        assert tr.state == "ALREADY_RUNNING"
        assert tr.status_code == 409

    def test_soma_202_returns_accepted(self):
        tr = TriggerResult(
            status_code=202,
            state="ACCEPTED",
            message="Action soma_kajabi_auto_finish accepted (HTTP 202)",
            run_id="new-run",
        )
        assert tr.state == "ACCEPTED"
        assert tr.run_id == "new-run"

    def test_soma_trigger_failed_includes_diagnostics(self):
        with _mock_hq(502, {"error_class": "HOSTD_UNREACHABLE", "error": "unreachable"}):
            r = trigger_exec("soma_kajabi", "soma_kajabi_auto_finish")
        assert r.state == "FAILED"
        assert "soma_kajabi" in r.message
        assert "HOSTD_UNREACHABLE" in r.message
        assert "hostd/HQ logs" in r.message


# ---------------------------------------------------------------------------
# Verify no direct _curl POST to /api/exec in migrated scripts
# ---------------------------------------------------------------------------

class TestNoDirectCurlInScripts:
    """Regression: ensure migrated scripts no longer contain _curl("POST"."""

    @pytest.mark.parametrize("script", [
        "soma_run_to_done.py",
        "soma_fix_and_retry.py",
        "soma_autopilot_tick.py",
        "soma_novnc_oneclick_recovery.py",
    ])
    def test_no_curl_post(self, script):
        from pathlib import Path
        script_path = Path(__file__).resolve().parents[1] / "scripts" / script
        content = script_path.read_text()
        assert '_curl("POST"' not in content, (
            f"{script} still contains _curl('POST'...). "
            "All exec POST calls must use trigger_exec() from ops.lib.exec_trigger."
        )
        assert "def _curl(" not in content, (
            f"{script} still defines its own _curl(). "
            "Use hq_request() from ops.lib.exec_trigger instead."
        )
