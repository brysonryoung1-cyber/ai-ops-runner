from __future__ import annotations

from ops.lib.notifier import build_alert_hash


def test_alert_hash_stable_across_failed_check_order() -> None:
    h1 = build_alert_hash(
        event_type="PASS_TO_FAIL",
        matrix_status="FAIL",
        failed_checks=["B", "A", "B"],
    )
    h2 = build_alert_hash(
        event_type="PASS_TO_FAIL",
        matrix_status="FAIL",
        failed_checks=["A", "B"],
    )
    assert h1 == h2


def test_alert_hash_changes_on_event_type() -> None:
    h1 = build_alert_hash(
        event_type="PASS_TO_FAIL",
        matrix_status="FAIL",
        failed_checks=["A"],
    )
    h2 = build_alert_hash(
        event_type="FAIL_TO_PASS",
        matrix_status="PASS",
        failed_checks=[],
    )
    assert h1 != h2

