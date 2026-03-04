#!/usr/bin/env python3
"""Path-compatible wrapper for system.doctor_matrix."""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from system.doctor_matrix import main as _main
from system.doctor_matrix import run_doctor_matrix as _run_doctor_matrix


def run_doctor_matrix(argv: list[str] | None = None):
    prev = os.environ.get("OPENCLAW_REPO_ROOT")
    os.environ["OPENCLAW_REPO_ROOT"] = str(REPO_ROOT)
    try:
        return _run_doctor_matrix(argv)
    finally:
        if prev is None:
            os.environ.pop("OPENCLAW_REPO_ROOT", None)
        else:
            os.environ["OPENCLAW_REPO_ROOT"] = prev


def main(argv: list[str] | None = None) -> int:
    prev = os.environ.get("OPENCLAW_REPO_ROOT")
    os.environ["OPENCLAW_REPO_ROOT"] = str(REPO_ROOT)
    try:
        return _main(argv)
    finally:
        if prev is None:
            os.environ.pop("OPENCLAW_REPO_ROOT", None)
        else:
            os.environ["OPENCLAW_REPO_ROOT"] = prev


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

