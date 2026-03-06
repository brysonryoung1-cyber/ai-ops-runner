from __future__ import annotations

from ops.system.soma_preflight import _derive_canary_contract


def test_ask_unreachable_is_optional_when_explicit_split_present() -> None:
    core, optional = _derive_canary_contract(
        {
            "status": "PASS",
            "core_status": "PASS",
            "optional_status": "WARN",
            "core_failed_checks": [],
            "optional_failed_checks": ["ask_unreachable"],
            "checks": {
                "ask_unreachable": {
                    "status": "WARN",
                    "severity": "OPTIONAL",
                }
            },
        }
    )
    assert core.status == "PASS"
    assert optional.status == "WARN"
    assert optional.details["failed_checks"] == ["ask_unreachable"]


def test_ask_unreachable_is_optional_in_legacy_result_fallback() -> None:
    core, optional = _derive_canary_contract(
        {
            "status": "DEGRADED",
            "failed_invariant": "ask_unreachable",
        }
    )
    assert core.status == "PASS"
    assert optional.status == "WARN"
    assert optional.details["failed_checks"] == ["ask_unreachable"]


def test_core_failure_stays_core_degraded() -> None:
    core, optional = _derive_canary_contract(
        {
            "status": "DEGRADED",
            "failed_invariant": "novnc_audit_failed",
        }
    )
    assert core.status == "FAIL"
    assert "novnc_audit_failed" in core.details["failed_checks"]
    assert optional.status in {"PASS", "WARN"}
