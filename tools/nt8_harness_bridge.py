"""Bridge: read job dir (confirm_spec + artifact_dir), run mock or real NT8, write tier2/ artifacts.

When MOCK_HARNESS=1 or no real NT8 automation is available, writes stub artifacts and done.json.
Production defaults to fail-closed (no live connections).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from tools.tier2_artifacts import Tier2Artifacts


def _read_job_dir(job_dir: str | Path) -> tuple[Path, dict]:
    """Read job_dir/artifact_dir.txt and confirm_spec.json. Returns (artifact_dir Path, spec dict)."""
    job = Path(job_dir)
    ad_path = job / "artifact_dir.txt"
    spec_path = job / "confirm_spec.json"
    if not ad_path.exists():
        raise FileNotFoundError(f"artifact_dir.txt not found in job dir: {job_dir}")
    artifact_dir = Path(ad_path.read_text().strip())
    if not spec_path.exists():
        raise FileNotFoundError(f"confirm_spec.json not found in job dir: {job_dir}")
    spec = json.loads(spec_path.read_text())
    return artifact_dir, spec


def run_harness(job_dir: str | Path) -> int:
    """
    Process one job: read spec, run mock or real NT8, write tier2/done.json and related artifacts.
    Returns exit code (0=ok, 1=gate/error, 3=stub).
    """
    artifact_dir, spec = _read_job_dir(job_dir)
    candidate_id = spec.get("candidate_id", "unknown")
    arts = Tier2Artifacts(artifact_dir, candidate_id)
    arts.ensure_dirs()

    mock = os.environ.get("MOCK_HARNESS", "").strip().lower() == "1"
    if mock or True:  # No real NT8 automation in this repo; use stub (fail-closed)
        arts.write_stub_artifacts("NT8_AUTOMATION_NOT_IMPLEMENTED")
        return 3
    # Future: invoke NT8 AddOn/CLI, wait for raw_exports, then normalize
    arts.write_stub_artifacts("NT8_AUTOMATION_NOT_IMPLEMENTED")
    return 3


def main() -> int:
    job_dir = os.environ.get("OPENCLAW_TIER2_JOB_DIR")
    if not job_dir or not Path(job_dir).is_dir():
        return 2
    return run_harness(job_dir)


if __name__ == "__main__":
    import sys
    sys.exit(main())
