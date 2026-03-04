from __future__ import annotations

from ops.system.brain_loop import decide_event


def test_pass_to_fail_alerts() -> None:
    event_type, should_alert = decide_event(
        prev_state={"matrix_status": "PASS", "failed_checks": []},
        matrix_status="FAIL",
        failed_checks=["CORE.HQ"],
        alert_on_first_fail=True,
    )
    assert event_type == "PASS_TO_FAIL"
    assert should_alert is True


def test_fail_to_pass_alerts() -> None:
    event_type, should_alert = decide_event(
        prev_state={"matrix_status": "FAIL", "failed_checks": ["CORE.HQ"]},
        matrix_status="PASS",
        failed_checks=[],
        alert_on_first_fail=True,
    )
    assert event_type == "FAIL_TO_PASS"
    assert should_alert is True


def test_fail_changed_checks_alerts() -> None:
    event_type, should_alert = decide_event(
        prev_state={"matrix_status": "FAIL", "failed_checks": ["A"]},
        matrix_status="FAIL",
        failed_checks=["B"],
        alert_on_first_fail=True,
    )
    assert event_type == "FAIL_CHECKS_CHANGED"
    assert should_alert is True


def test_fail_unchanged_checks_no_alert() -> None:
    event_type, should_alert = decide_event(
        prev_state={"matrix_status": "FAIL", "failed_checks": ["A", "B"]},
        matrix_status="FAIL",
        failed_checks=["B", "A"],
        alert_on_first_fail=True,
    )
    assert event_type is None
    assert should_alert is False


def test_first_run_pass_no_alert() -> None:
    event_type, should_alert = decide_event(
        prev_state=None,
        matrix_status="PASS",
        failed_checks=[],
        alert_on_first_fail=True,
    )
    assert event_type is None
    assert should_alert is False


def test_first_run_fail_alert_configurable() -> None:
    event_type_on, should_alert_on = decide_event(
        prev_state=None,
        matrix_status="FAIL",
        failed_checks=["A"],
        alert_on_first_fail=True,
    )
    event_type_off, should_alert_off = decide_event(
        prev_state=None,
        matrix_status="FAIL",
        failed_checks=["A"],
        alert_on_first_fail=False,
    )
    assert (event_type_on, should_alert_on) == ("FIRST_FAIL", True)
    assert (event_type_off, should_alert_off) == (None, False)

