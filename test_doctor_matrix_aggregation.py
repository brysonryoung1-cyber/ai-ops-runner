"""Unit tests for doctor matrix aggregation and summary rendering."""

from __future__ import annotations

from ops.lib.doctor_matrix.models import CheckResult
from ops.lib.doctor_matrix.summary import aggregate_matrix_result, render_summary_markdown


def _check(
    *,
    check_id: str,
    scope: str,
    project: str | None,
    status: str,
) -> CheckResult:
    return CheckResult(
        id=check_id,
        scope=scope,  # type: ignore[arg-type]
        project=project,
        status=status,  # type: ignore[arg-type]
        message=f"{check_id} {status}",
        error_class=None,
        started_at="2026-03-04T12:00:00+00:00",
        finished_at="2026-03-04T12:00:01+00:00",
        duration_ms=1000,
        evidence_paths=[f"evidence/{check_id}.json"],
        details={},
    )


def test_aggregate_matrix_result_counts_and_failures() -> None:
    checks = [
        _check(check_id="CORE.HQ_HEALTH", scope="core", project=None, status="PASS"),
        _check(check_id="CORE.HOSTD_STATUS", scope="core", project=None, status="FAIL"),
        _check(
            check_id="PROJECT.SOMA_POINTER_PRESENT",
            scope="project",
            project="soma_kajabi",
            status="PASS",
        ),
    ]

    result = aggregate_matrix_result(
        run_id="doctor_matrix_20260304T120000Z_123",
        checks=checks,
        started_at="2026-03-04T12:00:00+00:00",
        finished_at="2026-03-04T12:00:10+00:00",
        git_sha="abc1234",
        bundle_dir="/tmp/doctor",
    )

    assert result.status == "FAIL"
    assert result.failed_checks == ["CORE.HOSTD_STATUS"]
    assert result.core_summary == {"total": 2, "pass": 1, "fail": 1}
    assert result.project_summary["soma_kajabi"] == {"total": 1, "pass": 1, "fail": 0}


def test_summary_markdown_contains_checklist_table() -> None:
    checks = [
        _check(check_id="CORE.HQ_HEALTH", scope="core", project=None, status="PASS"),
        _check(check_id="CORE.HOSTD_STATUS", scope="core", project=None, status="FAIL"),
    ]
    result = aggregate_matrix_result(
        run_id="doctor_matrix_20260304T120000Z_123",
        checks=checks,
        started_at="2026-03-04T12:00:00+00:00",
        finished_at="2026-03-04T12:00:10+00:00",
        git_sha="abc1234",
        bundle_dir="/tmp/doctor",
    )

    summary = render_summary_markdown(result, checks)

    assert "| Checklist | Scope | Check ID | Status | Message |" in summary
    assert "| [x] | core | CORE.HQ_HEALTH | PASS |" in summary
    assert "| [ ] | core | CORE.HOSTD_STATUS | FAIL |" in summary
    assert "`CORE.HOSTD_STATUS`" in summary
