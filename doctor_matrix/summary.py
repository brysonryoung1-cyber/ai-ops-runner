"""Aggregation + summary rendering for doctor matrix."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Iterable

from .models import CheckResult, MatrixResult


def aggregate_matrix_result(
    *,
    run_id: str,
    checks: Iterable[CheckResult],
    started_at: str,
    finished_at: str,
    git_sha: str,
    bundle_dir: str,
) -> MatrixResult:
    check_list = list(checks)
    failed_checks = [check.id for check in check_list if check.status != "PASS"]
    status = "PASS" if len(failed_checks) == 0 else "FAIL"

    core_summary = {"total": 0, "pass": 0, "fail": 0}
    project_summary: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "pass": 0, "fail": 0})

    for check in check_list:
        if check.scope == "core":
            core_summary["total"] += 1
            if check.status == "PASS":
                core_summary["pass"] += 1
            else:
                core_summary["fail"] += 1
        else:
            project = check.project or "unknown_project"
            project_summary[project]["total"] += 1
            if check.status == "PASS":
                project_summary[project]["pass"] += 1
            else:
                project_summary[project]["fail"] += 1

    duration_ms = _duration_ms(started_at, finished_at)

    return MatrixResult(
        run_id=run_id,
        status=status,  # type: ignore[arg-type]
        failed_checks=failed_checks,
        core_summary=core_summary,
        project_summary=dict(project_summary),
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=duration_ms,
        git_sha=git_sha,
        bundle_dir=bundle_dir,
    )


def render_summary_markdown(result: MatrixResult, checks: Iterable[CheckResult]) -> str:
    rows = []
    for check in checks:
        mark = "[x]" if check.status == "PASS" else "[ ]"
        scope = check.scope if check.scope == "core" else f"project:{check.project or 'unknown'}"
        msg = (check.message or "").replace("\n", " ").strip()
        if len(msg) > 140:
            msg = msg[:140] + "..."
        rows.append(f"| {mark} | {scope} | {check.id} | {check.status} | {msg} |")

    failed_block = "\n".join([f"- `{cid}`" for cid in result.failed_checks]) if result.failed_checks else "- (none)"

    lines = [
        f"# Doctor Matrix — {result.run_id}",
        "",
        f"- Status: **{result.status}**",
        f"- Started (UTC): `{result.started_at}`",
        f"- Finished (UTC): `{result.finished_at}`",
        f"- Duration: `{result.duration_ms} ms`",
        f"- Git SHA: `{result.git_sha}`",
        "",
        "## Failed Checks",
        "",
        failed_block,
        "",
        "## Checklist",
        "",
        "| Checklist | Scope | Check ID | Status | Message |",
        "| --- | --- | --- | --- | --- |",
        *rows,
        "",
    ]
    return "\n".join(lines)


def _duration_ms(started_at: str, finished_at: str) -> int:
    try:
        start = datetime.fromisoformat(started_at)
        finish = datetime.fromisoformat(finished_at)
        return int((finish - start).total_seconds() * 1000)
    except ValueError:
        return 0
