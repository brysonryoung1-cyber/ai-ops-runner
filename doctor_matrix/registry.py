"""Registry for core doctor matrix checks."""

from __future__ import annotations

from .checks.core_artifacts import core_artifact_checks
from .checks.core_platform import core_platform_checks
from .runtime import CheckSpec


def get_core_checks() -> list[CheckSpec]:
    """Return deterministic core checks order."""

    return [
        *core_platform_checks(),
        *core_artifact_checks(),
    ]
