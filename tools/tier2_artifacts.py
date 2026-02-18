"""Deterministic Tier-2 artifact writer.

Produces the canonical artifact tree:
    tier2/
    ├── results.csv        (one row per candidate)
    ├── summary.json       (metadata, PASS/FAIL, reasons, best_candidate)
    ├── raw_exports/       (copy-through folder)
    └── done.json          (run_id, timestamps, exit_code, PASS/FAIL)
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RESULTS_COLUMNS = [
    "candidate_id",
    "pnl",
    "pf",
    "sharpe",
    "max_dd",
    "trades",
    "winrate",
    "avg_trade",
    "expectancy",
    "profit_factor",
    "time_in_market",
]

SUMMARY_SCHEMA_VERSION = "tier2_summary.v2"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _deterministic_run_id(candidate_id: str, output_dir: str) -> str:
    """Stable run_id derived from candidate + output_dir so re-runs are idempotent."""
    digest = hashlib.sha256(f"{candidate_id}:{output_dir}".encode()).hexdigest()[:12]
    return f"t2-{candidate_id}-{digest}"


class Tier2Artifacts:
    """Writes the deterministic Tier-2 artifact set."""

    def __init__(self, output_dir: str | Path, candidate_id: str) -> None:
        self.output_dir = Path(output_dir)
        self.tier2_dir = self.output_dir / "tier2"
        self.candidate_id = candidate_id
        self.run_id = _deterministic_run_id(candidate_id, str(self.output_dir))
        self._started_at = _now_iso()

    def ensure_dirs(self) -> None:
        self.tier2_dir.mkdir(parents=True, exist_ok=True)
        (self.tier2_dir / "raw_exports").mkdir(exist_ok=True)

    def write_results_csv(
        self,
        rows: list[dict[str, Any]] | None = None,
    ) -> Path:
        """Write results.csv. If rows is None, writes header-only (stub)."""
        p = self.tier2_dir / "results.csv"
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=RESULTS_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows or []:
            writer.writerow(row)
        p.write_text(buf.getvalue())
        return p

    def write_summary(
        self,
        verdict: str,
        reasons: list[str],
        best_candidate: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> Path:
        p = self.tier2_dir / "summary.json"
        doc: dict[str, Any] = {
            "schema_version": SUMMARY_SCHEMA_VERSION,
            "run_id": self.run_id,
            "candidate_id": self.candidate_id,
            "verdict": verdict,
            "reasons": reasons,
        }
        if best_candidate is not None:
            doc["best_candidate"] = best_candidate
        if extra:
            doc.update(extra)
        p.write_text(json.dumps(doc, indent=2) + "\n")
        return p

    def write_done(self, exit_code: int, status: str) -> Path:
        p = self.tier2_dir / "done.json"
        doc = {
            "done": True,
            "run_id": self.run_id,
            "candidate_id": self.candidate_id,
            "status": status,
            "exit_code": exit_code,
            "started_at": self._started_at,
            "finished_at": _now_iso(),
        }
        p.write_text(json.dumps(doc, indent=2) + "\n")
        return p

    def copy_raw_exports(self, src_dir: str | Path) -> None:
        """Copy raw NT8 exports into raw_exports/."""
        src = Path(src_dir)
        if not src.is_dir():
            return
        dst = self.tier2_dir / "raw_exports"
        for item in src.iterdir():
            if item.is_file():
                shutil.copy2(item, dst / item.name)
            elif item.is_dir():
                shutil.copytree(item, dst / item.name, dirs_exist_ok=True)

    def write_stub_artifacts(self, reason: str) -> None:
        """Write the complete artifact skeleton for a Phase-0 stub run."""
        self.ensure_dirs()
        self.write_results_csv()
        self.write_summary(
            verdict=reason,
            reasons=[reason],
            extra={"error_class": reason},
        )
        self.write_done(exit_code=3, status=reason)
