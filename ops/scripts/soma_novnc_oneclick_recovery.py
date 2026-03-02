#!/usr/bin/env python3
"""One-click Recovery: Fix noVNC + optionally resume Soma.

Chain: fix routing/frontdoor → novnc_doctor → ws_probe.

If current Soma state is WAITING_FOR_HUMAN: stop and report READY_FOR_HUMAN with canonical URL.
If NOT WAITING_FOR_HUMAN: run soma_run_to_done after fix.

Exit 0 on success. Fail-closed on fix/doctor/ws_probe failure.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

# Shared trigger client — single source of truth for exec POST + status handling
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from ops.lib.exec_trigger import hq_request, trigger_exec  # noqa: E402

HQ_BASE = os.environ.get("OPENCLAW_HQ_BASE", "http://127.0.0.1:8787")


def _get_soma_state() -> str:
    code, body = hq_request("GET", "/api/projects/soma_kajabi/status", timeout=15)
    if code != 200:
        return "unknown"
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return "unknown"
    return (data.get("last_status") or data.get("current_status") or "unknown")


def main() -> int:
    state = _get_soma_state()
    if state == "WAITING_FOR_HUMAN":
        print("State=WAITING_FOR_HUMAN: running fix only (no resume)")
    else:
        print(f"State={state}: running fix then soma_run_to_done")

    # 1. Fix routing (installs frontdoor, single-root serve, doctor, ws_probe)
    fix_script = ROOT / "ops" / "scripts" / "openclaw_novnc_routing_fix.sh"
    if not fix_script.exists():
        print("ERROR: openclaw_novnc_routing_fix.sh not found", file=sys.stderr)
        return 1
    rc = subprocess.run(
        ["bash", str(fix_script)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=300,
    )
    if rc.returncode != 0:
        print("FAIL: openclaw_novnc_routing_fix failed", file=sys.stderr)
        print(rc.stderr or rc.stdout, file=sys.stderr)
        return 1

    # Extract canonical URL from proof
    novnc_url = ""
    for d in sorted((ROOT / "artifacts" / "hq_proofs" / "frontdoor_fix").iterdir(), reverse=True):
        if d.is_dir():
            proof = d / "PROOF.md"
            if proof.exists():
                for line in proof.read_text().splitlines():
                    if line.startswith("https://") and "novnc" in line:
                        novnc_url = line.strip()
                        break
            if novnc_url:
                break

    if not novnc_url:
        novnc_url = "https://aiops-1.tailc75c62.ts.net/novnc/vnc.html?autoconnect=1&reconnect=true&reconnect_delay=2000&path=/websockify"

    if state == "WAITING_FOR_HUMAN":
        print("READY_FOR_HUMAN: login/2FA can now be completed")
        print(f"Canonical noVNC URL: {novnc_url}")
        return 0

    # 2. Resume soma_run_to_done — uses shared trigger client
    tr = trigger_exec("soma_kajabi", "soma_run_to_done")
    if tr.state == "ACCEPTED":
        print("soma_run_to_done started")
        return 0
    if tr.state == "ALREADY_RUNNING":
        print(f"Run already in progress for project=soma_kajabi. Not starting a second run.")
        return 0
    print(f"WARNING: soma_run_to_done trigger {tr.state} (HTTP {tr.status_code})", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
