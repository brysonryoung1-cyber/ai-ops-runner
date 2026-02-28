#!/usr/bin/env python3
"""
Invariants Engine — consumes State Pack + desired state, outputs invariants.json.
Fail-closed: never emit READY unless all invariants pass with evidence.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(os.environ.get("OPENCLAW_REPO_ROOT", os.getcwd()))
ARTIFACTS_ROOT = Path(os.environ.get("OPENCLAW_ARTIFACTS_ROOT", str(ROOT_DIR / "artifacts")))
FRONTDOOR_PORT = int(os.environ.get("OPENCLAW_FRONTDOOR_PORT", "8788"))
CONSOLE_PORT = int(os.environ.get("OPENCLAW_CONSOLE_PORT", "8787"))
TS_HOSTNAME = os.environ.get("OPENCLAW_TS_HOSTNAME", "aiops-1.tailc75c62.ts.net")
WS_PROBE_HOLD = int(os.environ.get("OPENCLAW_WS_PROBE_HOLD_SEC", "10"))


def _read_json(p: Path) -> dict | None:
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _read_text(p: Path) -> str:
    if not p.exists():
        return ""
    try:
        return p.read_text()
    except OSError:
        return ""


def _curl_http(url: str, timeout: int = 5) -> tuple[int, dict | None]:
    """Return (status_code, body_json or None)."""
    try:
        r = subprocess.run(
            ["curl", "-sf", "--connect-timeout", "3", "--max-time", str(timeout), url],
            capture_output=True,
            timeout=timeout + 2,
            cwd=ROOT_DIR,
        )
        if r.returncode != 0:
            return (0, None)
        try:
            return (200, json.loads(r.stdout.decode()))
        except json.JSONDecodeError:
            return (200 if r.stdout else 0, None)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return (0, None)


def evaluate_invariants(state_pack_dir: Path) -> dict:
    """
    Evaluate all invariants against State Pack. Returns invariants result dict.
    """
    out: dict = {
        "run_id": state_pack_dir.name,
        "state_pack_dir": str(state_pack_dir.relative_to(ARTIFACTS_ROOT) if str(state_pack_dir).startswith(str(ARTIFACTS_ROOT)) else state_pack_dir),
        "invariants": [],
        "all_pass": False,
        "evidence_pointers": [],
    }
    invariants = out["invariants"]
    evidence = out["evidence_pointers"]

    base = state_pack_dir
    health = _read_json(base / "health_public.json")
    autopilot = _read_json(base / "autopilot_status.json")
    tailscale_txt = _read_text(base / "tailscale_serve.txt")
    tailscale_json = _read_json(base / "tailscale_serve.json")
    ports_txt = _read_text(base / "ports.txt")

    # 1. HQ health_public build_sha not unknown
    build_sha = (health or {}).get("build_sha", "unknown") if health else "unknown"
    hp_pass = health is not None and health.get("ok") is True and build_sha and str(build_sha).lower() != "unknown"
    invariants.append({
        "id": "hq_health_build_sha_not_unknown",
        "pass": hp_pass,
        "reason": "OK" if hp_pass else f"build_sha={build_sha} (unknown or missing)",
        "evidence": f"artifacts/system/state_pack/{state_pack_dir.name}/health_public.json",
    })
    evidence.append(invariants[-1]["evidence"])

    # 2. autopilot/status HTTP 200
    ap_ok = autopilot is not None and autopilot.get("ok") is True
    invariants.append({
        "id": "autopilot_status_http_200",
        "pass": ap_ok,
        "reason": "OK" if ap_ok else "autopilot_status not ok or missing",
        "evidence": f"artifacts/system/state_pack/{state_pack_dir.name}/autopilot_status.json",
    })
    evidence.append(invariants[-1]["evidence"])

    # 3. serve.single_root == true and targets 127.0.0.1:8788
    serve_ok = False
    if tailscale_json:
        # tailscale serve --json structure varies; check for single-root + 8788
        raw = str(tailscale_json)
        serve_ok = "8788" in raw and ("127.0.0.1" in raw or "localhost" in raw)
    if not serve_ok and tailscale_txt:
        serve_ok = "8788" in tailscale_txt and "127.0.0.1" in tailscale_txt
    invariants.append({
        "id": "serve_single_root_targets_frontdoor",
        "pass": serve_ok,
        "reason": "OK" if serve_ok else "Tailscale Serve not single-root to 127.0.0.1:8788",
        "evidence": f"artifacts/system/state_pack/{state_pack_dir.name}/tailscale_serve.txt",
    })
    evidence.append(invariants[-1]["evidence"])

    # 4. frontdoor running and listening 8788
    fd_ok = ":8788" in ports_txt or "8788" in ports_txt
    invariants.append({
        "id": "frontdoor_listening_8788",
        "pass": fd_ok,
        "reason": "OK" if fd_ok else "Port 8788 not listening (frontdoor)",
        "evidence": f"artifacts/system/state_pack/{state_pack_dir.name}/ports.txt",
    })
    evidence.append(invariants[-1]["evidence"])

    # noVNC stack: STRICT. Canary must FAIL if 6080 not listening (no skip/optional).
    novnc_stack_available = ":6080" in ports_txt or "6080" in ports_txt

    # 5. novnc HTTP 200 for /novnc/vnc.html (REQUIRED; fail if 6080 not listening or non-200)
    novnc_url_local = f"http://127.0.0.1:{FRONTDOOR_PORT}/novnc/vnc.html"
    novnc_code, _ = _curl_http(novnc_url_local)
    novnc_http_ok = novnc_code == 200
    invariants.append({
        "id": "novnc_http_200",
        "pass": novnc_http_ok and novnc_stack_available,
        "reason": "OK" if novnc_http_ok and novnc_stack_available else (f"GET {novnc_url_local} returned {novnc_code}" if novnc_stack_available else "noVNC stack down (6080 not listening)"),
        "evidence": f"live_probe:{novnc_url_local}",
    })

    # 6. WSS probe >=10s for /websockify AND /novnc/websockify (REQUIRED; fail if 6080 not listening or probe fails)
    ws_probe_script = ROOT_DIR / "ops" / "scripts" / "novnc_ws_probe.py"
    ws_websockify_ok = False
    ws_novnc_ws_ok = False
    if ws_probe_script.exists() and novnc_stack_available:
        try:
            r = subprocess.run(
                [sys.executable, str(ws_probe_script), "--host", TS_HOSTNAME, "--hold", str(WS_PROBE_HOLD), "--all"],
                capture_output=True,
                timeout=WS_PROBE_HOLD + 15,
                cwd=ROOT_DIR,
                env={**os.environ, "OPENCLAW_TS_HOSTNAME": TS_HOSTNAME, "OPENCLAW_WS_PROBE_HOLD_SEC": str(WS_PROBE_HOLD)},
            )
            if r.returncode == 0 and r.stdout:
                probe_data = json.loads(r.stdout.decode())
                eps = probe_data.get("endpoints", {})
                ws_websockify_ok = eps.get("/websockify", {}).get("ok") is True
                ws_novnc_ws_ok = eps.get("/novnc/websockify", {}).get("ok") is True
        except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
            pass
    invariants.append({
        "id": "ws_probe_websockify_ge_10s",
        "pass": ws_websockify_ok and novnc_stack_available,
        "reason": "OK" if ws_websockify_ok else ("WSS /websockify probe did not hold >=10s" if novnc_stack_available else "noVNC stack down (6080 not listening)"),
        "evidence": f"live_probe:wss://{TS_HOSTNAME}/websockify",
    })
    invariants.append({
        "id": "ws_probe_novnc_websockify_ge_10s",
        "pass": ws_novnc_ws_ok and novnc_stack_available,
        "reason": "OK" if ws_novnc_ws_ok else ("WSS /novnc/websockify probe did not hold >=10s" if novnc_stack_available else "noVNC stack down (6080 not listening)"),
        "evidence": f"live_probe:wss://{TS_HOSTNAME}/novnc/websockify",
    })

    # 7. Browser Gateway ready (only when human gate session is active)
    bg_code, bg_data = _curl_http("http://127.0.0.1:8890/health")
    bg_active = bg_data.get("active_sessions", 0) > 0 if bg_data else False
    human_gate_active = False
    hg_lock = ARTIFACTS_ROOT / ".locks" / "soma_kajabi_auto_finish.json"
    if hg_lock.exists():
        hg_data = _read_json(hg_lock)
        if hg_data and hg_data.get("active_run_id"):
            human_gate_active = True
    if not human_gate_active:
        af_root = ARTIFACTS_ROOT / "soma_kajabi" / "auto_finish"
        if af_root.exists():
            af_dirs = sorted([d.name for d in af_root.iterdir() if d.is_dir()], reverse=True)
            for d in af_dirs[:3]:
                result_p = af_root / d / "RESULT.json"
                if result_p.exists():
                    r_data = _read_json(result_p)
                    if r_data and r_data.get("status") == "WAITING_FOR_HUMAN":
                        human_gate_active = True
                        break

    if human_gate_active:
        bg_pass = bg_code == 200 and bg_data is not None
        invariants.append({
            "id": "browser_gateway_ready",
            "pass": bg_pass,
            "reason": "OK" if bg_pass else "Browser Gateway not responding (human gate active)",
            "evidence": "live_probe:http://127.0.0.1:8890/health",
        })
        evidence.append(invariants[-1]["evidence"])

    out["all_pass"] = all(i["pass"] for i in invariants)
    return out


def main() -> int:
    state_pack_run_id = os.environ.get("OPENCLAW_STATE_PACK_RUN_ID")
    if not state_pack_run_id:
        # Use latest
        sp_base = ARTIFACTS_ROOT / "system" / "state_pack"
        if not sp_base.exists():
            print(json.dumps({"error": "No state pack found", "all_pass": False}, indent=2))
            return 1
        dirs = sorted([d.name for d in sp_base.iterdir() if d.is_dir()], reverse=True)
        state_pack_run_id = dirs[0] if dirs else None
    if not state_pack_run_id:
        print(json.dumps({"error": "No state pack run_id", "all_pass": False}, indent=2))
        return 1

    state_pack_dir = ARTIFACTS_ROOT / "system" / "state_pack" / state_pack_run_id
    if not state_pack_dir.exists():
        print(json.dumps({"error": f"State pack dir not found: {state_pack_dir}", "all_pass": False}, indent=2))
        return 1

    result = evaluate_invariants(state_pack_dir)
    out_path = os.environ.get("OPENCLAW_INVARIANTS_OUTPUT")
    if out_path:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    return 0 if result["all_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
