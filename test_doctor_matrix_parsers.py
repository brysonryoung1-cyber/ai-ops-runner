"""Unit tests for doctor matrix parser helpers."""

from __future__ import annotations

import json

from ops.lib.doctor_matrix.parsers import (
    detect_browse_pagination,
    parse_pointer_from_browse_response,
)


def test_detect_browse_pagination_below_cap() -> None:
    payload = {
        "entries": [
            {"name": "a", "type": "dir"},
            {"name": "b", "type": "dir"},
        ]
    }
    parsed = detect_browse_pagination(payload)
    assert parsed["parse_ok"] is True
    assert parsed["entries_count"] == 2
    assert parsed["likely_capped"] is False
    assert parsed["has_pagination_fields"] is False


def test_detect_browse_pagination_cap_without_fields() -> None:
    payload = {
        "entries": [{"name": f"n{i}", "type": "dir"} for i in range(200)]
    }
    parsed = detect_browse_pagination(payload)
    assert parsed["parse_ok"] is True
    assert parsed["likely_capped"] is True
    assert parsed["has_pagination_fields"] is False
    assert parsed["reason"] == "entry_cap_hit_without_pagination_fields"


def test_detect_browse_pagination_cap_with_fields() -> None:
    payload = {
        "entries": [{"name": f"n{i}", "type": "dir"} for i in range(200)],
        "has_more": True,
        "next_cursor": "cursor-1",
    }
    parsed = detect_browse_pagination(payload)
    assert parsed["parse_ok"] is True
    assert parsed["likely_capped"] is True
    assert parsed["has_pagination_fields"] is True
    assert "has_more" in parsed["pagination_fields"]


def test_parse_pointer_from_browse_response_reuses_content_parser() -> None:
    body = json.dumps(
        {
            "content": json.dumps(
                {
                    "run_id": "20260304120000-abcd",
                    "run_dir": "run_to_done_20260304T120000Z_mock1234",
                    "status": "SUCCESS",
                }
            ),
            "contentType": "json",
            "fileName": "LATEST_RUN.json",
            "entries": [],
        }
    )
    parsed = parse_pointer_from_browse_response(body)
    assert parsed is not None
    assert parsed["run_id"] == "20260304120000-abcd"
    assert parsed["status"] == "SUCCESS"


def test_parse_pointer_from_browse_response_invalid() -> None:
    body = json.dumps({"entries": []})
    parsed = parse_pointer_from_browse_response(body)
    assert parsed is None
