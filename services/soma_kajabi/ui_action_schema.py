"""Strict schema validation for bounded LLM UI fallback actions.

Fail-closed parser:
- only known action types
- only allowlisted targets
- only allowlisted typed values for fill/wait actions
- bounded max steps
"""

from __future__ import annotations

import json
from typing import Any, Mapping


ALLOWED_ACTION_TYPES = {"goto", "click", "fill", "wait_for_text"}


class ActionSchemaError(ValueError):
    """Raised when LLM action JSON fails strict schema validation."""


def _extract_json(raw: str) -> Any:
    text = (raw or "").strip()
    if not text:
        raise ActionSchemaError("empty_llm_payload")

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ActionSchemaError(f"invalid_json_object: {exc}") from exc

    start = text.find("[")
    end = text.rfind("]")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ActionSchemaError(f"invalid_json_array: {exc}") from exc

    raise ActionSchemaError("no_json_found")


def _as_action_list(parsed: Any) -> list[dict[str, Any]]:
    if isinstance(parsed, list):
        raw_actions = parsed
    elif isinstance(parsed, dict):
        extra_keys = set(parsed.keys()) - {"actions"}
        if extra_keys:
            raise ActionSchemaError(f"unexpected_root_keys: {sorted(extra_keys)}")
        raw_actions = parsed.get("actions")
    else:
        raise ActionSchemaError("root_must_be_object_or_array")

    if not isinstance(raw_actions, list):
        raise ActionSchemaError("actions_must_be_list")

    out: list[dict[str, Any]] = []
    for idx, item in enumerate(raw_actions):
        if not isinstance(item, dict):
            raise ActionSchemaError(f"action[{idx}]_must_be_object")
        out.append(item)
    return out


def parse_llm_actions(
    raw: str,
    *,
    target_allowlist: Mapping[str, Mapping[str, Any]],
    max_steps: int,
) -> list[dict[str, Any]]:
    """Parse and validate LLM action JSON against an explicit allowlist."""
    if max_steps <= 0:
        raise ActionSchemaError("max_steps_must_be_positive")

    parsed = _extract_json(raw)
    actions = _as_action_list(parsed)
    if len(actions) == 0:
        raise ActionSchemaError("empty_actions")
    if len(actions) > max_steps:
        raise ActionSchemaError(f"too_many_actions:{len(actions)}>{max_steps}")

    normalized: list[dict[str, Any]] = []
    for idx, action in enumerate(actions):
        allowed_keys = {"action", "target", "value"}
        extra_keys = set(action.keys()) - allowed_keys
        if extra_keys:
            raise ActionSchemaError(f"action[{idx}]_unexpected_keys:{sorted(extra_keys)}")

        action_type = action.get("action")
        target = action.get("target")
        value = action.get("value")

        if not isinstance(action_type, str):
            raise ActionSchemaError(f"action[{idx}]_action_must_be_string")
        if action_type not in ALLOWED_ACTION_TYPES:
            raise ActionSchemaError(f"action[{idx}]_action_not_allowed:{action_type}")

        if not isinstance(target, str):
            raise ActionSchemaError(f"action[{idx}]_target_must_be_string")
        if target not in target_allowlist:
            raise ActionSchemaError(f"action[{idx}]_target_not_allowlisted:{target}")

        spec = target_allowlist[target]
        allowed_target_actions = spec.get("actions") or ()
        if action_type not in set(allowed_target_actions):
            raise ActionSchemaError(
                f"action[{idx}]_action_not_allowed_for_target:{action_type}:{target}"
            )

        typed_values = set(spec.get("typed_values") or ())
        if action_type in {"fill", "wait_for_text"}:
            if not isinstance(value, str) or not value:
                raise ActionSchemaError(f"action[{idx}]_value_required_for_{action_type}")
            if typed_values and value not in typed_values:
                raise ActionSchemaError(
                    f"action[{idx}]_value_not_allowlisted:{value}:{sorted(typed_values)}"
                )
        elif value is not None:
            raise ActionSchemaError(f"action[{idx}]_value_not_allowed_for_{action_type}")

        normalized.append(
            {
                "action": action_type,
                "target": target,
                "value": value,
            }
        )

    return normalized

