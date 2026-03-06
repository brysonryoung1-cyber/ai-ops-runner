"""Unit tests for ops/scripts/novnc_backend_vnc_probe.py."""

from __future__ import annotations

import importlib.util
import json
import socket
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "novnc_backend_vnc_probe",
        REPO_ROOT / "ops" / "scripts" / "novnc_backend_vnc_probe.py",
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class _FakeConn:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_probe_success_writes_artifacts(tmp_path, monkeypatch) -> None:
    mod = _load_module()

    def _ok_connect(*args, **kwargs):  # noqa: ANN002,ANN003
        return _FakeConn()

    monkeypatch.setattr(mod.socket, "create_connection", _ok_connect)
    rc = mod.main(["--artifact-dir", str(tmp_path), "--timeout-sec", "1"])
    assert rc == 0

    status = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    details = json.loads((tmp_path / "details.json").read_text(encoding="utf-8"))
    assert status["status"] == "PASS"
    assert status["ok"] is True
    assert details["ok"] is True
    assert details["port"] == 5900


def test_probe_failure_writes_error_artifacts(tmp_path, monkeypatch) -> None:
    mod = _load_module()

    def _fail_connect(*args, **kwargs):  # noqa: ANN002,ANN003
        raise socket.timeout("timed out")

    monkeypatch.setattr(mod.socket, "create_connection", _fail_connect)
    rc = mod.main(["--artifact-dir", str(tmp_path), "--timeout-sec", "1"])
    assert rc == 1

    status = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    details = json.loads((tmp_path / "details.json").read_text(encoding="utf-8"))
    assert status["status"] == "FAIL"
    assert status["ok"] is False
    assert "timed out" in (details.get("error") or "")
