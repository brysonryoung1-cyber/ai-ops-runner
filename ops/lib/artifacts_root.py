"""Canonical artifacts-root resolver.

Single source of truth for determining the artifacts directory across all
scripts (soma_run_to_done, doctor_matrix, etc.).

Priority:
  1. OPENCLAW_ARTIFACTS_ROOT env var (if set and non-empty)
  2. /opt/ai-ops-runner/artifacts (if it exists — canonical VPS path)
  3. <repo_root>/artifacts (local dev fallback)
"""

from __future__ import annotations

import os
from pathlib import Path

_VPS_ARTIFACTS_ROOT = Path("/opt/ai-ops-runner/artifacts")


def get_artifacts_root(repo_root: Path | None = None) -> Path:
    """Return the canonical artifacts root directory.

    Parameters
    ----------
    repo_root:
        Fallback repo root for local-dev resolution. When *None*, the
        function still honours the env-var and VPS paths; the repo
        fallback becomes ``Path.cwd() / "artifacts"`` (rarely hit in
        practice because callers normally supply the repo root).
    """
    env = os.environ.get("OPENCLAW_ARTIFACTS_ROOT", "").strip()
    if env:
        return Path(env)

    if _VPS_ARTIFACTS_ROOT.exists():
        return _VPS_ARTIFACTS_ROOT

    if repo_root is not None:
        return repo_root / "artifacts"

    return Path.cwd() / "artifacts"
