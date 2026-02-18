"""Backtest-only enforcement gate.

Fail-closed logic:
  1. BACKTEST_ONLY must be true in the topk spec (schema enforces this).
  2. An explicit local config flag must confirm backtest mode.
  3. Known NT8 connection indicators are checked if available.

If any check cannot be proven safe, the gate fails closed.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class GateResult:
    passed: bool
    error_class: str = ""
    message: str = ""
    checks: list[str] = field(default_factory=list)


_NT8_CONNECTION_FILES = [
    "connections.xml",
    "Connections.xml",
]

def check_backtest_only_gate(
    topk_backtest_only: bool,
    nt8_user_dir: str | None = None,
) -> GateResult:
    """Run fail-closed checks. Returns GateResult with pass/fail."""
    checks: list[str] = []

    if topk_backtest_only is not True:
        return GateResult(
            passed=False,
            error_class="BACKTEST_ONLY_REQUIRED",
            message="BACKTEST_ONLY must be true in topk.json",
            checks=["BACKTEST_ONLY: FAIL"],
        )
    checks.append("BACKTEST_ONLY: PASS")

    env_flag = os.environ.get("BACKTEST_ONLY", "").lower()
    if env_flag != "true":
        return GateResult(
            passed=False,
            error_class="BACKTEST_ONLY_ENV_MISSING",
            message="Environment variable BACKTEST_ONLY must be set to 'true'",
            checks=checks + ["BACKTEST_ONLY_ENV: FAIL"],
        )
    checks.append("BACKTEST_ONLY_ENV: PASS")

    if nt8_user_dir:
        nt8_dir = Path(nt8_user_dir)
        if nt8_dir.is_dir():
            found_any_conn_file = False
            for conn_file in _NT8_CONNECTION_FILES:
                conn_path = nt8_dir / conn_file
                if conn_path.is_file():
                    found_any_conn_file = True
                    try:
                        content = conn_path.read_text(errors="replace")
                    except OSError:
                        return GateResult(
                            passed=False,
                            error_class="LIVE_CONNECTIONS_UNKNOWN",
                            message=f"Cannot read NT8 connections file: {conn_path}",
                            checks=checks + [f"NT8_CONNECTIONS({conn_path}): UNKNOWN"],
                        )
                    lower = content.lower()
                    has_simulated = "simulated" in lower or "playback" in lower

                    _LIVE_INDICATORS = [
                        "cqg", "rithmic", "interactive brokers", "ib gateway",
                        "td ameritrade", "schwab", "tradovate", "dorman",
                        "continuum", "gain", "fxcm", "oanda",
                    ]
                    live_found = [ind for ind in _LIVE_INDICATORS if ind in lower]

                    if live_found:
                        return GateResult(
                            passed=False,
                            error_class="LIVE_CONNECTIONS_DETECTED",
                            message=f"Connection config at {conn_path} contains live provider(s): {', '.join(live_found)}",
                            checks=checks + [f"NT8_CONNECTIONS({conn_path}): FAIL (live providers: {', '.join(live_found)})"],
                        )
                    if not has_simulated:
                        return GateResult(
                            passed=False,
                            error_class="LIVE_CONNECTIONS_UNKNOWN",
                            message=f"Connection config at {conn_path} has no recognizable simulated/playback entries",
                            checks=checks + [f"NT8_CONNECTIONS({conn_path}): UNKNOWN"],
                        )
                    checks.append(f"NT8_CONNECTIONS({conn_path}): PASS")
            if not found_any_conn_file:
                return GateResult(
                    passed=False,
                    error_class="LIVE_CONNECTIONS_UNKNOWN",
                    message=f"NT8 dir exists at {nt8_dir} but no connections.xml found — cannot prove safe",
                    checks=checks + ["NT8_DIR_SCAN: FAIL (no connection files)"],
                )
            checks.append("NT8_DIR_SCAN: PASS")
        else:
            return GateResult(
                passed=False,
                error_class="LIVE_CONNECTIONS_UNKNOWN",
                message=f"NT8 user dir specified but not found: {nt8_dir} — cannot prove safe",
                checks=checks + [f"NT8_DIR_SCAN: FAIL (dir not found: {nt8_dir})"],
            )
    else:
        checks.append("NT8_DIR_SCAN: SKIPPED (no nt8_user_dir provided)")

    return GateResult(passed=True, checks=checks)
