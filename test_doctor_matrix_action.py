"""Registration tests for doctor matrix action + plugin file presence."""

from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent


def test_system_doctor_matrix_action_registered() -> None:
    data = json.loads((REPO_ROOT / "config" / "action_registry.json").read_text(encoding="utf-8"))
    actions = {item.get("id"): item for item in data.get("actions", [])}
    assert "system.doctor.matrix" in actions
    cmd = str(actions["system.doctor.matrix"].get("cmd_template", ""))
    assert "ops/system/doctor_matrix.py" in cmd


def test_soma_doctor_plugin_exists() -> None:
    assert (REPO_ROOT / "services" / "soma_kajabi" / "doctor_plugin.py").exists()
