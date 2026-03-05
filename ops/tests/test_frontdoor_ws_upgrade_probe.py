"""Tests for the localhost frontdoor websocket upgrade probe."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PROBE_SCRIPT = REPO_ROOT / "ops" / "scripts" / "frontdoor_ws_upgrade_probe.py"


def _load_probe_module():
    spec = importlib.util.spec_from_file_location("frontdoor_ws_upgrade_probe", PROBE_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeSocket:
    def __init__(self, responses: dict[str, tuple[str, dict[str, str]]]) -> None:
        self._responses = responses
        self._request = b""
        self._served = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def settimeout(self, timeout: float) -> None:
        _ = timeout

    def sendall(self, data: bytes) -> None:
        self._request += data

    def recv(self, size: int) -> bytes:
        _ = size
        if self._served:
            return b""
        self._served = True
        path = self._request.decode("iso-8859-1", errors="replace").split(" ", 2)[1]
        status_line, headers = self._responses.get(path, ("HTTP/1.1 404 Not Found", {"Server": "mock"}))
        response = status_line + "\r\n"
        for key, value in headers.items():
            response += f"{key}: {value}\r\n"
        response += "Content-Length: 0\r\n\r\n"
        return response.encode("ascii")

    def close(self) -> None:
        return None


def test_frontdoor_ws_upgrade_probe_passes_and_writes_artifacts(tmp_path, monkeypatch) -> None:
    module = _load_probe_module()
    responses = {
        "/websockify": (
            "HTTP/1.1 101 Switching Protocols",
            {
                "Connection": "Upgrade",
                "Upgrade": "websocket",
                "Sec-WebSocket-Accept": "mock-accept",
                "Server": "mock-frontdoor",
            },
        ),
        "/novnc/websockify": (
            "HTTP/1.1 101 Switching Protocols",
            {
                "Connection": "Upgrade",
                "Upgrade": "websocket",
                "Sec-WebSocket-Accept": "mock-accept",
                "Server": "mock-frontdoor",
            },
        ),
    }

    def fake_create_connection(address, timeout=5.0):
        _ = address, timeout
        return FakeSocket(responses)

    monkeypatch.setattr(module.socket, "create_connection", fake_create_connection)
    artifact_dir = tmp_path / "artifact"
    rc = module.main(["--host", "127.0.0.1", "--port", "8788", "--artifact-dir", str(artifact_dir)])

    assert rc == 0
    result_json = json.loads((artifact_dir / "result.json").read_text())
    assert result_json["all_ok"] is True
    assert result_json["message"] == "PASS"
    assert (artifact_dir / "websockify_headers.txt").read_text().startswith("HTTP/1.1 101")
    assert (artifact_dir / "novnc_websockify_headers.txt").read_text().startswith("HTTP/1.1 101")


def test_frontdoor_ws_upgrade_probe_fails_with_clear_message(tmp_path, monkeypatch) -> None:
    module = _load_probe_module()
    responses = {
        "/websockify": (
            "HTTP/1.1 101 Switching Protocols",
            {
                "Connection": "Upgrade",
                "Upgrade": "websocket",
                "Sec-WebSocket-Accept": "mock-accept",
                "Server": "mock-frontdoor",
            },
        ),
        "/novnc/websockify": (
            "HTTP/1.1 404 Not Found",
            {"Server": "mock-frontdoor"},
        ),
    }

    def fake_create_connection(address, timeout=5.0):
        _ = address, timeout
        return FakeSocket(responses)

    monkeypatch.setattr(module.socket, "create_connection", fake_create_connection)
    artifact_dir = tmp_path / "artifact"
    rc = module.main(["--host", "127.0.0.1", "--port", "8788", "--artifact-dir", str(artifact_dir)])

    assert rc == 1
    result_json = json.loads((artifact_dir / "result.json").read_text())
    assert result_json["all_ok"] is False
    assert result_json["message"] == "frontdoor_ws_upgrade_failed:/novnc/websockify:HTTP_404"
    failing = next(item for item in result_json["results"] if item["path"] == "/novnc/websockify")
    assert failing["status_code"] == 404
