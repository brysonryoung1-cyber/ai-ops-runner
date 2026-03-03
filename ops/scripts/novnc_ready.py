"""Shared noVNC readiness adapter used by interactive Soma/Kajabi scripts.

This module keeps the legacy tuple API while delegating to the convergent,
self-healing readiness gate in ops.lib.novnc_readiness.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops.lib.novnc_readiness import (  # noqa: E402
    build_canonical_novnc_url,
    ensure_novnc_ready as _ensure_convergent,
)


def novnc_display() -> str:
    """Canonical DISPLAY from /etc/ai-ops-runner/config/novnc_display.env."""
    cfg = Path("/etc/ai-ops-runner/config/novnc_display.env")
    if cfg.exists():
        for line in cfg.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line.startswith("DISPLAY=") and "=" in line:
                return line.split("=", 1)[1].strip().strip("'\"") or ":99"
    return os.environ.get("OPENCLAW_NOVNC_DISPLAY", os.environ.get("DISPLAY", ":99")) or ":99"


CANONICAL_PARAMS = "autoconnect=1&reconnect=true&reconnect_delay=2000&path=/websockify"


def _build_canonical_url(host: str) -> str:
    """Build canonical HTTPS noVNC URL for a given host."""
    return build_canonical_novnc_url(host)


def _write_pointer(artifact_dir: Path, payload: dict) -> None:
    try:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        (artifact_dir / "novnc_readiness_pointer.json").write_text(
            json.dumps(payload, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass


def ensure_novnc_ready(artifact_dir: Path, run_id: str) -> tuple[bool, str, str | None, str | None]:
    """Legacy adapter returning (ready, novnc_url, error_class, journal_artifact)."""
    out = _ensure_convergent(run_id=run_id, mode="deep", emit_artifacts=True)
    pointer = {
        "ok": out.ok,
        "run_id": out.run_id,
        "error_class": out.error_class,
        "novnc_url": out.novnc_url,
        "novnc_readiness_artifact_dir": out.readiness_artifact_dir,
        "journal_artifact": out.journal_artifact,
    }
    _write_pointer(Path(artifact_dir), pointer)
    if out.ok:
        return True, out.novnc_url, None, None
    return False, out.novnc_url, out.error_class or "NOVNC_NOT_READY", out.journal_artifact


def ensure_novnc_ready_with_recovery(artifact_dir: Path, run_id: str) -> tuple[bool, str, str | None, str | None]:
    """Legacy API; recovery is already integrated into the convergent gate."""
    return ensure_novnc_ready(artifact_dir, run_id)
