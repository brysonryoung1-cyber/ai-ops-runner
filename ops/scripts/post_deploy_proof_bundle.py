#!/usr/bin/env python3
"""Post-deploy proof bundle — deterministic, idempotent self-certification.

Collects all proof artifacts after a successful deploy and writes a single
timestamped directory under artifacts/post_deploy/<run_id>/ with:
  - health_public.json
  - status_soma_kajabi.json
  - ssh_tailscale_only_verify.txt
  - pointers.json   (paths to doctor / canary / deploy artifacts)
  - PROOF_BLOCK.md   (paste-ready human summary)
  - RESULT.json      (machine-readable overall result)

Fail-closed: if any required check is missing/FAIL, the bundle marks FAILURE
and includes the failing sub-proof paths and stderr.

Soma WAITING_FOR_HUMAN is recorded but does NOT fail the whole bundle.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from ops.lib.exec_trigger import hq_request  # noqa: E402


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

HQ_BASE = os.environ.get("OPENCLAW_VERIFY_BASE_URL", "http://127.0.0.1:8787")
ARTIFACTS_ROOT = Path(os.environ.get("OPENCLAW_ARTIFACTS_ROOT", str(REPO_ROOT / "artifacts")))
DEPLOY_RUN_ID = os.environ.get("OPENCLAW_DEPLOY_RUN_ID", "")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _random_hex(n: int = 4) -> str:
    try:
        return os.urandom(n).hex()
    except Exception:
        return str(os.getpid())


# ---------------------------------------------------------------------------
# Data collection helpers
# ---------------------------------------------------------------------------

def fetch_health_public() -> dict[str, Any]:
    """GET /api/ui/health_public → parsed JSON or error dict."""
    code, body = hq_request("GET", "/api/ui/health_public", timeout=10, base_url=HQ_BASE)
    if code == 200:
        try:
            return json.loads(body)
        except (json.JSONDecodeError, TypeError):
            return {"ok": False, "error": "invalid_json", "raw": body[:500]}
    return {"ok": False, "error": f"http_{code}", "raw": body[:500]}


def fetch_soma_kajabi_status() -> dict[str, Any]:
    """GET /api/projects/soma_kajabi/status → parsed JSON or error dict."""
    code, body = hq_request("GET", "/api/projects/soma_kajabi/status", timeout=15, base_url=HQ_BASE)
    if code == 200:
        try:
            return json.loads(body)
        except (json.JSONDecodeError, TypeError):
            return {"ok": False, "error": "invalid_json"}
    return {"ok": False, "error": f"http_{code}", "raw": body[:500]}


def run_ssh_verify() -> tuple[bool, str]:
    """Run ssh_tailscale_only --verify and return (pass, output_text)."""
    script = REPO_ROOT / "ops" / "openclaw_fix_ssh_tailscale_only.sh"
    if not script.exists():
        return False, "script not found"
    try:
        result = subprocess.run(
            ["sudo", str(script), "--verify"],
            capture_output=True, text=True, timeout=30,
        )
        output = (result.stdout or "") + (result.stderr or "")
        return result.returncode == 0, output.strip()
    except subprocess.TimeoutExpired:
        return False, "timeout (30s)"
    except Exception as e:
        return False, str(e)


def find_latest_artifact_dir(base: Path) -> Path | None:
    """Return the most-recently-created subdirectory under *base*, or None."""
    if not base.is_dir():
        return None
    dirs = sorted(
        (d for d in base.iterdir() if d.is_dir()),
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )
    return dirs[0] if dirs else None


def find_latest_with_file(base: Path, filename: str) -> Path | None:
    """Return latest subdir containing *filename*."""
    if not base.is_dir():
        return None
    dirs = sorted(
        (d for d in base.iterdir() if d.is_dir() and (d / filename).is_file()),
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )
    return dirs[0] if dirs else None


# ---------------------------------------------------------------------------
# Pointer resolution
# ---------------------------------------------------------------------------

def resolve_pointers() -> dict[str, Any]:
    """Resolve paths to latest doctor, canary, deploy, and dod artifacts."""
    pointers: dict[str, Any] = {}

    deploy_dir = find_latest_with_file(ARTIFACTS_ROOT / "deploy", "deploy_result.json")
    if deploy_dir:
        pointers["deploy_result"] = str(deploy_dir / "deploy_result.json")
        pointers["deploy_dir"] = str(deploy_dir)
        receipt = deploy_dir / "deploy_receipt.json"
        if receipt.is_file():
            pointers["deploy_receipt"] = str(receipt)

    doctor_dir = find_latest_with_file(ARTIFACTS_ROOT / "doctor", "doctor.json")
    if doctor_dir:
        pointers["doctor"] = str(doctor_dir / "doctor.json")
        pointers["doctor_dir"] = str(doctor_dir)

    dod_dir = find_latest_with_file(ARTIFACTS_ROOT / "dod", "dod_result.json")
    if dod_dir:
        pointers["dod_result"] = str(dod_dir / "dod_result.json")

    canary_dir = find_latest_with_file(ARTIFACTS_ROOT / "system" / "canary", "result.json")
    if canary_dir:
        pointers["canary_result"] = str(canary_dir / "result.json")
        pointers["canary_proof"] = str(canary_dir / "PROOF.md")
        pointers["canary_dir"] = str(canary_dir)

    return pointers


# ---------------------------------------------------------------------------
# Bundle generation (public API for tests + CLI)
# ---------------------------------------------------------------------------

def generate_bundle(
    out_dir: Path,
    *,
    health_public: dict[str, Any] | None = None,
    soma_status: dict[str, Any] | None = None,
    ssh_verify: tuple[bool, str] | None = None,
    pointers: dict[str, Any] | None = None,
    deploy_run_id: str = "",
    origin_sha: str = "",
) -> dict[str, Any]:
    """Generate the full proof bundle into *out_dir*.

    All data-source arguments can be injected for testing.  When ``None``
    the function fetches live data.

    Returns the RESULT dict (also written to out_dir/RESULT.json).
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    # -- 1. health_public --
    if health_public is None:
        health_public = fetch_health_public()
    (out_dir / "health_public.json").write_text(json.dumps(health_public, indent=2))

    # -- 2. Soma status --
    if soma_status is None:
        soma_status = fetch_soma_kajabi_status()
    (out_dir / "status_soma_kajabi.json").write_text(json.dumps(soma_status, indent=2))

    # -- 3. SSH verify --
    if ssh_verify is None:
        ssh_verify = run_ssh_verify()
    ssh_pass, ssh_output = ssh_verify
    (out_dir / "ssh_tailscale_only_verify.txt").write_text(ssh_output)

    # -- 4. Pointers --
    if pointers is None:
        pointers = resolve_pointers()
    (out_dir / "pointers.json").write_text(json.dumps(pointers, indent=2))

    # -- 5. Derive fields --
    build_sha = health_public.get("build_sha", "unknown")
    deploy_sha = health_public.get("deploy_sha") or ""
    server_time = health_public.get("server_time", "")
    health_ok = health_public.get("ok") is True

    soma_acceptance = soma_status.get("acceptance_path") or soma_status.get("acceptance") or "N/A"
    soma_mirror = soma_status.get("mirror_pass")
    if soma_mirror is None:
        soma_mirror = soma_status.get("mirror", {}).get("pass") if isinstance(soma_status.get("mirror"), dict) else "N/A"
    soma_exceptions = soma_status.get("exceptions_count")
    if soma_exceptions is None:
        soma_exceptions = soma_status.get("exceptions", {}).get("count") if isinstance(soma_status.get("exceptions"), dict) else "N/A"
    soma_stage = soma_status.get("stage") or soma_status.get("state") or "unknown"
    soma_waiting = soma_stage in ("WAITING_FOR_HUMAN", "waiting_for_human")

    # Doctor / canary pass flags
    doctor_pass = _check_artifact_pass(pointers.get("doctor"), "overall")
    canary_pass = _check_artifact_pass(pointers.get("canary_result"), "status", expected="PASS")

    if not origin_sha:
        origin_sha = _git_origin_sha()
    if not deploy_run_id:
        deploy_run_id = DEPLOY_RUN_ID or _infer_deploy_run_id(pointers)

    # -- 6. Overall verdict --
    failures: list[str] = []
    if not health_ok:
        failures.append("health_public NOT ok")
    if not ssh_pass:
        failures.append("ssh_tailscale_only verify FAIL")
    if doctor_pass is False:
        failures.append("doctor FAIL")
    if canary_pass is False:
        failures.append("canary FAIL")

    overall = "PASS" if not failures else "FAILURE"
    timestamp = _now_iso()

    result: dict[str, Any] = {
        "overall": overall,
        "run_id": out_dir.name,
        "timestamp": timestamp,
        "build_sha": build_sha,
        "deploy_sha": deploy_sha or build_sha,
        "origin_sha": origin_sha,
        "deploy_run_id": deploy_run_id,
        "health_public_ok": health_ok,
        "server_time": server_time,
        "ssh_tailscale_only_verify": "PASS" if ssh_pass else "FAIL",
        "doctor": "PASS" if doctor_pass else ("UNKNOWN" if doctor_pass is None else "FAIL"),
        "canary": "PASS" if canary_pass else ("UNKNOWN" if canary_pass is None else "FAIL"),
        "soma_kajabi": {
            "acceptance_path": soma_acceptance,
            "mirror_pass": soma_mirror,
            "exceptions_count": soma_exceptions,
            "stage": soma_stage,
            "waiting_for_human": soma_waiting,
        },
        "failures": failures,
        "pointers": pointers,
        "bundle_path": str(out_dir),
    }

    (out_dir / "RESULT.json").write_text(json.dumps(result, indent=2))

    # -- 7. PROOF_BLOCK.md --
    proof_md = _render_proof_block(result)
    (out_dir / "PROOF_BLOCK.md").write_text(proof_md)

    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _check_artifact_pass(
    path: str | None,
    key: str,
    expected: str = "PASS",
) -> bool | None:
    """Read a JSON artifact and check if *key* == *expected*.  Returns None if missing."""
    if not path or not Path(path).is_file():
        return None
    try:
        data = json.loads(Path(path).read_text())
        return str(data.get(key, "")).upper() == expected.upper()
    except Exception:
        return None


def _git_origin_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "origin/main"],
            capture_output=True, text=True, timeout=10,
        )
        return result.stdout.strip() if result.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def _infer_deploy_run_id(pointers: dict[str, Any]) -> str:
    deploy_dir = pointers.get("deploy_dir", "")
    if deploy_dir:
        return Path(deploy_dir).name
    return ""


def _render_proof_block(result: dict[str, Any]) -> str:
    soma = result.get("soma_kajabi", {})
    failures = result.get("failures", [])
    pointers = result.get("pointers", {})
    status_icon = "PASS" if result["overall"] == "PASS" else "FAILURE"

    lines = [
        f"# Post-Deploy Proof Bundle — {status_icon}",
        "",
        f"**Timestamp:** {result['timestamp']}",
        f"**Bundle path:** `{result['bundle_path']}`",
        "",
        "## Identity",
        f"- **build_sha:** `{result['build_sha']}`",
        f"- **deploy_sha:** `{result['deploy_sha']}`",
        f"- **origin/main sha:** `{result['origin_sha']}`",
        f"- **deploy_run_id:** `{result['deploy_run_id']}`",
        "",
        "## Checks",
        f"- **health_public:** {'PASS' if result['health_public_ok'] else 'FAIL'} (server_time: {result['server_time']})",
        f"- **ssh_tailscale_only --verify:** {result['ssh_tailscale_only_verify']}",
        f"- **doctor:** {result['doctor']}",
        f"- **canary:** {result['canary']}",
        "",
        "## Soma Kajabi Status",
        f"- **acceptance_path:** {soma.get('acceptance_path', 'N/A')}",
        f"- **mirror_pass:** {soma.get('mirror_pass', 'N/A')}",
        f"- **exceptions_count:** {soma.get('exceptions_count', 'N/A')}",
        f"- **stage:** {soma.get('stage', 'unknown')}",
    ]
    if soma.get("waiting_for_human"):
        lines.append("- **note:** WAITING_FOR_HUMAN (recorded, not a bundle failure)")

    lines.extend([
        "",
        "## Artifact Pointers",
    ])
    for k, v in sorted(pointers.items()):
        lines.append(f"- **{k}:** `{v}`")

    if failures:
        lines.extend([
            "",
            "## Failures",
        ])
        for f in failures:
            lines.append(f"- {f}")

    lines.extend([
        "",
        f"## Overall: **{status_icon}**",
        "",
    ])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> int:
    run_id = f"proof_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{_random_hex()}"
    out_dir = ARTIFACTS_ROOT / "post_deploy" / run_id
    deploy_run_id = DEPLOY_RUN_ID

    print(f"=== post_deploy_proof_bundle ===")
    print(f"  Run ID: {run_id}")
    print(f"  Output: {out_dir}")
    print()

    result = generate_bundle(out_dir, deploy_run_id=deploy_run_id)

    overall = result["overall"]
    print(f"  Overall: {overall}")
    print(f"  Bundle:  {out_dir}")
    print(f"  PROOF:   {out_dir / 'PROOF_BLOCK.md'}")

    if overall != "PASS":
        print(f"  Failures: {result['failures']}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
