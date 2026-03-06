"""Tests for strict LLM UI fallback action schema."""

from __future__ import annotations

import pytest

from services.soma_kajabi.ui_action_schema import ActionSchemaError, parse_llm_actions


def _allowlist() -> dict[str, dict]:
    return {
        "goto_pages": {"actions": {"goto"}},
        "click_new_page": {"actions": {"click"}},
        "fill_slug": {"actions": {"fill"}, "typed_values": {"privacy-policy"}},
    }


def test_parse_llm_actions_accepts_valid_payload():
    raw = """{
      "actions": [
        {"action":"goto","target":"goto_pages"},
        {"action":"click","target":"click_new_page"},
        {"action":"fill","target":"fill_slug","value":"privacy-policy"}
      ]
    }"""
    actions = parse_llm_actions(raw, target_allowlist=_allowlist(), max_steps=5)
    assert len(actions) == 3
    assert actions[2]["value"] == "privacy-policy"


def test_parse_llm_actions_rejects_out_of_scope_target():
    raw = """{"actions":[{"action":"click","target":"delete_everything"}]}"""
    with pytest.raises(ActionSchemaError, match="target_not_allowlisted"):
        parse_llm_actions(raw, target_allowlist=_allowlist(), max_steps=3)


def test_parse_llm_actions_rejects_typed_value_not_allowlisted():
    raw = """{"actions":[{"action":"fill","target":"fill_slug","value":"../../etc/passwd"}]}"""
    with pytest.raises(ActionSchemaError, match="value_not_allowlisted"):
        parse_llm_actions(raw, target_allowlist=_allowlist(), max_steps=3)

