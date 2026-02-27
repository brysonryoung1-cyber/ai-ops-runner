"""Unit tests for code.opencode.propose_patch action."""
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ACTION_REGISTRY = REPO_ROOT / "config" / "action_registry.json"


def test_opencode_action_in_registry():
    """code.opencode.propose_patch is allowlisted in action_registry.json."""
    with open(ACTION_REGISTRY) as f:
        data = json.load(f)
    ids = [a["id"] for a in data.get("actions", [])]
    assert "code.opencode.propose_patch" in ids


def test_opencode_action_has_cmd_template():
    """code.opencode.propose_patch has cmd_template invoking opencode_run.sh."""
    with open(ACTION_REGISTRY) as f:
        data = json.load(f)
    for a in data.get("actions", []):
        if a.get("id") == "code.opencode.propose_patch":
            assert "opencode_run.sh" in a.get("cmd_template", "")
            assert a.get("timeout_sec", 0) >= 600
            return
    pytest.fail("code.opencode.propose_patch not found")
