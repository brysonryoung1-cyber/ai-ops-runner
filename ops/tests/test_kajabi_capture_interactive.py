"""Tests for kajabi_capture_interactive — noVNC stop gating."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_capture_module():
    script = REPO_ROOT / "ops" / "scripts" / "kajabi_capture_interactive.py"
    spec = importlib.util.spec_from_file_location("kajabi_capture_interactive", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def test_capture_interactive_waiting_does_not_stop_novnc():
    """On WAITING/ok=False, _stop_novnc_systemd is not called."""
    module = _load_capture_module()
    with mock.patch.object(module, "_stop_novnc_systemd") as stop_mock:
        module._maybe_stop_novnc_on_success({"ok": False, "status": "WAITING_FOR_HUMAN"})
        stop_mock.assert_not_called()


def test_capture_interactive_success_stops_novnc():
    """On success/ok=True, _stop_novnc_systemd is called."""
    module = _load_capture_module()
    with mock.patch.object(module, "_stop_novnc_systemd") as stop_mock:
        module._maybe_stop_novnc_on_success({"ok": True})
        stop_mock.assert_called_once()


def test_capture_interactive_has_storage_state_resolver():
    """capture_interactive uses _resolve_storage_state_path for path unification."""
    script = REPO_ROOT / "ops" / "scripts" / "kajabi_capture_interactive.py"
    content = script.read_text()
    assert "_resolve_storage_state_path" in content
    assert "get_storage_state_path" in content
