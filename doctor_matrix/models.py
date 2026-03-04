"""Data models for system doctor matrix."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

CheckScope = Literal["core", "project"]
CheckStatus = Literal["PASS", "FAIL"]


@dataclass(frozen=True)
class RunDirContract:
    """Deterministic run-dir mapping contract declared by a project plugin."""

    project: str
    pointer_relpath: str
    required_fields: tuple[str, ...] = ("run_id", "run_dir", "status")
    run_dir_field: str = "run_dir"


@dataclass
class CheckResult:
    """Normalized result for a single doctor matrix check."""

    id: str
    scope: CheckScope
    project: str | None
    status: CheckStatus
    message: str
    started_at: str
    finished_at: str
    duration_ms: int
    error_class: str | None = None
    evidence_paths: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "scope": self.scope,
            "project": self.project,
            "status": self.status,
            "error_class": self.error_class,
            "message": self.message,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": int(self.duration_ms),
            "evidence_paths": list(self.evidence_paths),
            "details": dict(self.details),
        }


@dataclass
class MatrixResult:
    """Top-level doctor matrix result."""

    run_id: str
    status: CheckStatus
    failed_checks: list[str]
    core_summary: dict[str, int]
    project_summary: dict[str, dict[str, int]]
    started_at: str
    finished_at: str
    duration_ms: int
    git_sha: str
    bundle_dir: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "failed_checks": list(self.failed_checks),
            "core_summary": dict(self.core_summary),
            "project_summary": {
                key: dict(value) for key, value in self.project_summary.items()
            },
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": int(self.duration_ms),
            "git_sha": self.git_sha,
            "bundle_dir": self.bundle_dir,
        }
