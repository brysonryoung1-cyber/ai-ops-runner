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
import sys
from pathlib import Path

from tools.validate_topk import validate_topk_file
from tools.backtest_gate import check_backtest_only_gate
from tools.tier2_artifacts import Tier2Artifacts

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

    # --- Step 3: Attempt NT8 automation (Phase-0 stub) ---
    # Future: wire to NT8 Strategy Analyzer or Walk-Forward Optimizer via
    # COM automation or CLI bridge. For now, produce the artifact skeleton
    # and exit with NT8_AUTOMATION_NOT_IMPLEMENTED.

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
