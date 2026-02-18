#!/usr/bin/env python3
"""Tier-2 Confirmation Harness Entrypoint.

Usage:
    python -m tools.tier2_confirm_entrypoint \\
        --topk /path/to/topk.json \\
        --output-dir /path/to/output \\
        --mode strategy_analyzer|walk_forward \\
        [--nt8-user-dir /path/to/NinjaTrader8]

Exit codes:
    0 = confirmation completed successfully
    1 = validation / gate error (artifacts written)
    2 = usage error
    3 = NT8_AUTOMATION_NOT_IMPLEMENTED (Phase-0 stub, artifacts written)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from tools.validate_topk import validate_topk_file
from tools.backtest_gate import check_backtest_only_gate
from tools.tier2_artifacts import Tier2Artifacts
from tools.confirm_spec import write_confirm_spec
from tools.nt8_export_normalizer import normalize_raw_exports

VALID_MODES = ("strategy_analyzer", "walk_forward")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Tier-2 Confirmation Harness",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--topk", required=True, help="Path to topk.json")
    p.add_argument("--output-dir", required=True, help="Output directory for artifacts")
    p.add_argument(
        "--mode",
        required=True,
        choices=VALID_MODES,
        help="Confirmation mode: strategy_analyzer or walk_forward",
    )
    p.add_argument(
        "--nt8-user-dir",
        default=None,
        help="Path to NinjaTrader 8 user data directory (for live connection checks)",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    topk_path = Path(args.topk)
    output_dir = Path(args.output_dir)

    # --- Step 1: Validate topk.json ---
    val_err = validate_topk_file(topk_path)
    if val_err:
        print(json.dumps({"stage": "validation", **val_err.to_dict()}), file=sys.stderr)
        try:
            data = json.loads(topk_path.read_text()) if topk_path.exists() else {}
            cid = data.get("candidate_id", "unknown")
        except Exception:
            cid = "unknown"
        arts = Tier2Artifacts(output_dir, cid)
        arts.ensure_dirs()
        arts.write_summary(
            verdict="FAIL",
            reasons=[val_err.message],
            extra={"error_class": val_err.error_class},
        )
        arts.write_results_csv()
        arts.write_done(exit_code=1, status="VALIDATION_FAILED")
        return 1

    with open(topk_path) as f:
        topk = json.load(f)

    candidate_id = topk["candidate_id"]
    arts = Tier2Artifacts(output_dir, candidate_id)
    arts.ensure_dirs()

    # --- Resumable: if done.json exists, no-op and return existing exit_code ---
    done_path = output_dir / "tier2" / "done.json"
    if done_path.exists():
        try:
            done_data = json.loads(done_path.read_text())
            return int(done_data.get("exit_code", 3))
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

    # --- Step 2: Backtest-only gate ---
    gate = check_backtest_only_gate(
        topk_backtest_only=topk.get("BACKTEST_ONLY", False),
        nt8_user_dir=args.nt8_user_dir,
    )
    if not gate.passed:
        print(
            json.dumps({"stage": "gate", "error_class": gate.error_class, "message": gate.message}),
            file=sys.stderr,
        )
        arts.write_summary(
            verdict="FAIL",
            reasons=[gate.message] + gate.checks,
            extra={"error_class": gate.error_class},
        )
        arts.write_results_csv()
        arts.write_done(exit_code=1, status=gate.error_class)
        return 1

    # --- Step 3: Write confirm_spec to job dir if hostd set OPENCLAW_TIER2_JOB_DIR ---
    job_dir = os.environ.get("OPENCLAW_TIER2_JOB_DIR")
    if job_dir:
        try:
            write_confirm_spec(args.topk, job_dir, mode=args.mode)
        except Exception as e:
            print(json.dumps({"stage": "confirm_spec", "error": str(e)}), file=sys.stderr)
            arts.write_summary(verdict="FAIL", reasons=[f"confirm_spec write failed: {e}"], extra={"error_class": "CONFIRM_SPEC_ERROR"})
            arts.write_results_csv()
            arts.write_done(exit_code=1, status="CONFIRM_SPEC_ERROR")
            return 1

    # --- Step 4: Run harness (mock or real NT8); if job dir not set, use Phase-0 stub ---
    if job_dir and Path(job_dir).is_dir():
        import subprocess
        proc = subprocess.run(
            [sys.executable, "-m", "tools.nt8_harness_bridge"],
            env={**os.environ, "OPENCLAW_TIER2_JOB_DIR": job_dir},
            capture_output=True,
            text=True,
            timeout=300,
        )
        if proc.returncode != 0 and proc.returncode != 3:
            arts.write_summary(verdict="FAIL", reasons=[proc.stderr or "harness failed"], extra={"error_class": "HARNESS_ERROR"})
            arts.write_results_csv()
            arts.write_done(exit_code=1, status="HARNESS_ERROR")
            return 1
        # If harness wrote raw_exports with parseable data, normalize into results.csv/summary
        norm_rows, norm_verdict, norm_reasons = normalize_raw_exports(arts, candidate_id)
        if norm_rows and norm_verdict == "PASS":
            arts.write_results_csv(norm_rows)
            arts.write_summary(verdict="PASS", reasons=norm_reasons or ["export normalized"], best_candidate=candidate_id)
            arts.write_done(exit_code=0, status="PASS")
            return 0
        # Stub or no parseable export: use what harness already wrote (done.json from bridge)
        if done_path.exists():
            try:
                return int(json.loads(done_path.read_text()).get("exit_code", 3))
            except (json.JSONDecodeError, ValueError, TypeError):
                pass
    # Phase-0 fallback: no job dir or harness not available
    stub_reason = "NT8_AUTOMATION_NOT_IMPLEMENTED"
    print(
        json.dumps({
            "stage": "execute",
            "mode": args.mode,
            "candidate_id": candidate_id,
            "status": stub_reason,
            "gate_checks": gate.checks,
        }),
    )
    arts.write_stub_artifacts(stub_reason)
    return 3


if __name__ == "__main__":
    sys.exit(main())
