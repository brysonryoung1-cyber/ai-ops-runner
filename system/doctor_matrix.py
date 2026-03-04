#!/usr/bin/env python3
"""System-wide Doctor Matrix (core + project plugins)."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ops.lib.doctor_matrix.plugins import discover_project_plugins
from ops.lib.doctor_matrix.registry import get_core_checks
from ops.lib.doctor_matrix.runtime import (
    MatrixRuntime,
    build_run_id,
    load_mock_fixture,
    now_utc_iso,
    resolve_artifacts_root,
    resolve_repo_root,
)
from ops.lib.doctor_matrix.summary import aggregate_matrix_result, render_summary_markdown


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run system doctor matrix")
    parser.add_argument(
        "--mode",
        choices=("core", "all"),
        default="all",
        help="core=core checks only, all=core + project plugins",
    )
    parser.add_argument(
        "--project",
        help="Run core + only this project plugin (e.g., soma_kajabi)",
        default="",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use hermetic mock fixtures instead of live HTTP",
    )
    parser.add_argument(
        "--mock-fixture",
        default="",
        help="Optional path to mock fixture JSON (default package fixture)",
    )
    parser.add_argument(
        "--run-id",
        default="",
        help="Optional run id override",
    )
    return parser.parse_args(argv)


def _get_git_sha(repo_root: Path) -> str:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            cwd=str(repo_root),
            timeout=5,
            check=False,
        )
        if proc.returncode == 0:
            return (proc.stdout or "").strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        pass
    return "unknown"


def _get_git_short_sha(repo_root: Path) -> str:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            capture_output=True,
            text=True,
            cwd=str(repo_root),
            timeout=5,
            check=False,
        )
        if proc.returncode == 0:
            return (proc.stdout or "").strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        pass
    return "unknown"


def _get_git_dirty(repo_root: Path) -> bool:
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            cwd=str(repo_root),
            timeout=5,
            check=False,
        )
        if proc.returncode == 0:
            return bool((proc.stdout or "").strip())
    except (OSError, subprocess.SubprocessError):
        pass
    return False


def _default_mock_fixture_path(repo_root: Path) -> Path:
    return repo_root / "ops" / "lib" / "doctor_matrix" / "fixtures" / "mock_http.json"


def _resolve_bases() -> tuple[str, str]:
    frontdoor_port = os.environ.get("OPENCLAW_FRONTDOOR_PORT", "8788")
    console_port = os.environ.get("OPENCLAW_CONSOLE_PORT", "8787")
    frontdoor = os.environ.get("OPENCLAW_HQ_BASE_FRONTDOOR", "").strip() or f"http://127.0.0.1:{frontdoor_port}"
    localhost = (
        os.environ.get("OPENCLAW_HQ_BASE_LOCALHOST", "").strip()
        or os.environ.get("OPENCLAW_HQ_BASE", "").strip()
        or f"http://127.0.0.1:{console_port}"
    )
    return frontdoor, localhost


def _build_env_summary(runtime: MatrixRuntime, args: argparse.Namespace) -> dict[str, Any]:
    project_filter = sorted(runtime.project_filter) if runtime.project_filter else []
    return {
        "timestamp_utc": now_utc_iso(),
        "repo_root": str(runtime.repo_root),
        "artifacts_root": str(runtime.artifacts_root),
        "bundle_dir": str(runtime.bundle_dir),
        "mode": args.mode,
        "mock": bool(args.mock),
        "project_filter": project_filter,
        "base_urls": runtime.base_urls,
        "python": {
            "version": sys.version.split()[0],
            "executable": sys.executable,
        },
        "platform": {
            "sys_platform": sys.platform,
            "pid": os.getpid(),
        },
        "env_flags": {
            "OPENCLAW_REPO_ROOT": bool(os.environ.get("OPENCLAW_REPO_ROOT")),
            "OPENCLAW_ARTIFACTS_ROOT": bool(os.environ.get("OPENCLAW_ARTIFACTS_ROOT")),
            "OPENCLAW_HQ_BASE": bool(os.environ.get("OPENCLAW_HQ_BASE")),
            "OPENCLAW_HQ_BASE_FRONTDOOR": bool(os.environ.get("OPENCLAW_HQ_BASE_FRONTDOOR")),
            "OPENCLAW_HQ_BASE_LOCALHOST": bool(os.environ.get("OPENCLAW_HQ_BASE_LOCALHOST")),
        },
    }


def _build_version_summary(repo_root: Path) -> dict[str, Any]:
    return {
        "timestamp_utc": now_utc_iso(),
        "git_sha": _get_git_sha(repo_root),
        "git_short_sha": _get_git_short_sha(repo_root),
        "git_dirty": _get_git_dirty(repo_root),
    }


def run_doctor_matrix(argv: list[str] | None = None) -> tuple[int, dict[str, Any]]:
    args = _parse_args(list(argv or []))

    if args.mode == "core" and args.project:
        payload = {
            "ok": False,
            "status": "FAIL",
            "error_class": "INVALID_ARGS",
            "message": "--project is only valid with --mode all",
        }
        return 2, payload

    repo_root = resolve_repo_root(Path(__file__))
    artifacts_root = resolve_artifacts_root(repo_root)
    run_id = (args.run_id or "").strip() or build_run_id()

    bundle_dir = artifacts_root / "system" / "doctor_matrix" / run_id
    frontdoor_base, localhost_base = _resolve_bases()

    mock_fixture: dict[str, Any] | None = None
    if args.mock:
        fixture_path = Path(args.mock_fixture).expanduser() if args.mock_fixture else _default_mock_fixture_path(repo_root)
        mock_fixture = load_mock_fixture(fixture_path)

    project_filter: set[str] | None = None
    if args.project:
        project_filter = {args.project.strip()}

    runtime = MatrixRuntime(
        repo_root=repo_root,
        artifacts_root=artifacts_root,
        bundle_dir=bundle_dir,
        frontdoor_base=frontdoor_base,
        localhost_base=localhost_base,
        run_id=run_id,
        mock=args.mock,
        mock_fixture=mock_fixture,
        mode=args.mode,
        project_filter=project_filter,
    )

    started_at = now_utc_iso()

    discovery = discover_project_plugins(
        repo_root,
        runtime,
        project_filter=project_filter,
    )

    run_dir_contracts = []
    for discovered in discovery.plugins:
        try:
            run_dir_contracts.extend(discovered.plugin.run_dir_contracts(runtime))
        except Exception as exc:  # noqa: BLE001
            builder = runtime.start_check(
                check_id=f"PROJECT.{discovered.project.upper()}.RUN_DIR_CONTRACTS_LOAD",
                scope="project",
                project=discovered.project,
            )
            builder.write_json(
                "error.json",
                {
                    "source": str(discovered.source),
                    "error": str(exc),
                },
            )

    runtime.run_dir_contracts = run_dir_contracts

    checks = []
    checks.extend(get_core_checks())

    if args.mode == "all":
        for discovered in discovery.plugins:
            checks.extend(discovered.plugin.checks(runtime))

    check_results = [runtime.execute(spec) for spec in checks]

    for err in discovery.errors:
        builder = runtime.start_check(
            check_id=f"PROJECT.{err.project.upper()}.PLUGIN_LOAD",
            scope="project",
            project=err.project,
        )
        builder.write_json(
            "plugin_load_error.json",
            {
                "source": err.source,
                "error": err.error,
            },
        )
        check_results.append(
            builder.finalize(
                status="FAIL",
                message=f"plugin discovery failed for {err.project}",
                error_class="PLUGIN_DISCOVERY_FAIL",
                details={"source": err.source},
            )
        )

    if args.project and args.mode == "all":
        requested = args.project.strip()
        loaded = {d.project for d in discovery.plugins}
        if requested not in loaded:
            builder = runtime.start_check(
                check_id=f"PROJECT.{requested.upper()}.PLUGIN_NOT_FOUND",
                scope="project",
                project=requested,
            )
            builder.write_json("plugin_not_found.json", {"requested_project": requested})
            check_results.append(
                builder.finalize(
                    status="FAIL",
                    message=f"requested project plugin not found: {requested}",
                    error_class="PROJECT_PLUGIN_NOT_FOUND",
                    details={"requested_project": requested},
                )
            )

    finished_at = now_utc_iso()
    git_sha = _get_git_short_sha(repo_root)

    matrix = aggregate_matrix_result(
        run_id=run_id,
        checks=check_results,
        started_at=started_at,
        finished_at=finished_at,
        git_sha=git_sha,
        bundle_dir=str(bundle_dir),
    )

    summary_md = render_summary_markdown(matrix, check_results)

    runtime.write_bundle_json("checks.json", [check.to_dict() for check in check_results])
    runtime.write_bundle_json("RESULT.json", matrix.to_dict())
    runtime.write_bundle_text("SUMMARY.md", summary_md)
    runtime.write_bundle_json("ENV.json", _build_env_summary(runtime, args))
    runtime.write_bundle_json("VERSION.json", _build_version_summary(repo_root))

    stdout_payload = {
        "ok": matrix.status == "PASS",
        **matrix.to_dict(),
        "checks_total": len(check_results),
    }

    exit_code = 0 if matrix.status == "PASS" else 1
    return exit_code, stdout_payload


def main(argv: list[str] | None = None) -> int:
    exit_code, payload = run_doctor_matrix(argv)
    print(json.dumps(payload, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
