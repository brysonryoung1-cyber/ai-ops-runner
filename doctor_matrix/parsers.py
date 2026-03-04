"""Parser helpers for doctor matrix checks."""

from __future__ import annotations

from typing import Any, Mapping

from ops.lib.aiops_remote_helpers import parse_artifact_browse_proof


def detect_browse_pagination(
    payload: Mapping[str, Any] | None,
    *,
    entry_cap: int = 200,
) -> dict[str, Any]:
    """Detect likely browse pagination/capping from API payload shape."""

    if not isinstance(payload, Mapping):
        return {
            "parse_ok": False,
            "entries_count": 0,
            "entry_cap": int(entry_cap),
            "likely_capped": False,
            "has_pagination_fields": False,
            "pagination_fields": [],
            "reason": "payload_not_object",
        }

    entries = payload.get("entries")
    if not isinstance(entries, list):
        return {
            "parse_ok": False,
            "entries_count": 0,
            "entry_cap": int(entry_cap),
            "likely_capped": False,
            "has_pagination_fields": False,
            "pagination_fields": [],
            "reason": "entries_missing_or_not_list",
        }

    pagination_fields = [
        key
        for key in (
            "has_more",
            "next_cursor",
            "cursor",
            "page",
            "page_size",
            "limit",
            "offset",
            "total",
            "total_count",
        )
        if key in payload
    ]

    count = len(entries)
    has_pagination_fields = len(pagination_fields) > 0
    likely_capped = count >= int(entry_cap)

    if likely_capped and not has_pagination_fields:
        reason = "entry_cap_hit_without_pagination_fields"
    elif likely_capped and has_pagination_fields:
        reason = "entry_cap_hit_with_pagination_fields"
    else:
        reason = "below_entry_cap"

    return {
        "parse_ok": True,
        "entries_count": count,
        "entry_cap": int(entry_cap),
        "likely_capped": likely_capped,
        "has_pagination_fields": has_pagination_fields,
        "pagination_fields": pagination_fields,
        "reason": reason,
    }


def parse_json_content_from_browse_response(body_text: str) -> dict[str, Any] | None:
    """Parse browse API file response JSON payload from its ``content`` field."""

    parsed = parse_artifact_browse_proof(body_text)
    return parsed if isinstance(parsed, dict) else None


def parse_pointer_from_browse_response(body_text: str) -> dict[str, Any] | None:
    """Parse a run-pointer JSON payload from browse response content."""

    parsed = parse_json_content_from_browse_response(body_text)
    return parsed if isinstance(parsed, dict) else None
