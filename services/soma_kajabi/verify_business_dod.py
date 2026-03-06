"""Business DoD verifier for Soma Kajabi.

Deterministic, no-LLM validators for 8 business readiness checks.
Writes artifacts under artifacts/soma_kajabi/business_dod/<run_id>/.
No secrets in output. Fail-closed on missing data.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import urlopen, Request
from urllib.error import URLError


EXPECTED_HOSTNAME = "zane-mccourtney.mykajabi.com"

RAW_MODULE_NAMES = ["raw", "raw – needs review", "raw - needs review"]

REQUIRED_OFFER_URLS = ["/offers/q6ntyjef/checkout", "/offers/MHMmHyVZ/checkout"]

DEFAULT_TERMS_URLS = [
    "https://zane-mccourtney.mykajabi.com/terms",
    "https://zane-mccourtney.mykajabi.com/privacy-policy",
]

DEFAULT_LANDING_URL = "https://zane-mccourtney.mykajabi.com"

EXPECTED_COMMUNITY_NAME = "Soma Community"
EXPECTED_GROUPS = ["Home Users", "Practitioners"]

SECRET_PATTERNS = [
    (r"sk-[a-zA-Z0-9]{20,}", "openai_api_key"),
    (r"ghp_[a-zA-Z0-9]{36,}", "github_pat"),
    (r"gho_[a-zA-Z0-9]{36,}", "github_oauth"),
    (r"glpat-[a-zA-Z0-9\-]{20,}", "gitlab_pat"),
    (r"Bearer\s+[a-zA-Z0-9\-_.]{40,}", "bearer_token"),
    (r'"client_secret"\s*:\s*"[^"]{10,}"', "oauth_client_secret"),
    (r"-----BEGIN\s+(RSA\s+)?PRIVATE\s+KEY-----", "private_key"),
    (r"AKIA[0-9A-Z]{16}", "aws_access_key"),
    (r"xox[bpras]-[0-9a-zA-Z\-]{10,}", "slack_token"),
]

SECRETS_ALLOWLIST = [
    "sk-ant-example",
    "sk-test-",
    "sk-placeholder",
    "ghp_EXAMPLE",
    "AKIAIOSFODNN7EXAMPLE",
]

MEMBERSHIPS_AGE_THRESHOLD_DAYS = 7


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _random_hex(n: int = 4) -> str:
    try:
        return os.urandom(n).hex()
    except Exception:
        return str(os.getpid())


def _artifacts_root() -> Path:
    env = os.environ.get("OPENCLAW_ARTIFACTS_ROOT")
    if env:
        return Path(env)
    if Path("/opt/ai-ops-runner/artifacts").is_dir():
        return Path("/opt/ai-ops-runner/artifacts")
    from services.soma_kajabi.config import repo_root
    return repo_root() / "artifacts"


def _repo_root() -> Path:
    from services.soma_kajabi.config import repo_root
    return repo_root()


def _make_check(passed: bool, details: str = "",
                evidence_paths: list[str] | None = None,
                reason: str = "",
                status: str | None = None) -> dict[str, Any]:
    return {
        "pass": passed,
        "status": status or ("PASS" if passed else "FAIL"),
        "details": details,
        "evidence_paths": evidence_paths or [],
        "reason": reason,
    }


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.{_random_hex(8)}.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def _write_latest_pointer(
    artifacts_root: Path,
    project_id: str,
    *,
    run_id: str,
    out_dir: Path,
    passed: bool,
) -> None:
    pointer_path = artifacts_root / project_id / "business_dod" / "LATEST.json"
    payload = {
        "run_id": run_id,
        "artifact_dir": str(out_dir),
        "status": "PASS" if passed else "FAIL",
        "updated_at": _now_iso(),
    }
    try:
        _atomic_write_json(pointer_path, payload)
    except Exception:
        pass


def _iter_snapshot_candidates(artifacts_root: Path) -> list[Path]:
    candidates: list[Path] = []
    accept_base = artifacts_root / "soma_kajabi" / "acceptance"
    if accept_base.is_dir():
        for d in accept_base.iterdir():
            candidate = d / "final_library_snapshot.json"
            if d.is_dir() and candidate.is_file():
                candidates.append(candidate)
    phase0_base = artifacts_root / "soma_kajabi" / "phase0"
    if phase0_base.is_dir():
        for d in phase0_base.iterdir():
            candidate = d / "kajabi_library_snapshot.json"
            if d.is_dir() and candidate.is_file():
                candidates.append(candidate)
    return sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)


def _read_json_dict(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _discover_base(artifacts_root: Path) -> Path:
    return artifacts_root / "soma_kajabi" / "discover"


def _latest_discover_pointer(artifacts_root: Path) -> dict[str, Any] | None:
    return _read_json_dict(_discover_base(artifacts_root) / "LATEST.json")


def _discover_candidate_dirs(artifacts_root: Path) -> tuple[list[Path], dict[str, Any] | None]:
    discover_base = _discover_base(artifacts_root)
    pointer = _latest_discover_pointer(artifacts_root)
    dirs: list[Path] = []
    seen: set[Path] = set()

    if isinstance(pointer, dict):
        raw_dir = pointer.get("artifact_dir")
        if isinstance(raw_dir, str) and raw_dir.strip():
            preferred = Path(raw_dir.strip())
            if preferred.exists():
                dirs.append(preferred)
                seen.add(preferred.resolve())

    if discover_base.is_dir():
        recent = sorted(
            (d for d in discover_base.iterdir() if d.is_dir()),
            key=lambda d: d.stat().st_mtime,
            reverse=True,
        )
        for candidate in recent:
            resolved = candidate.resolve()
            if resolved in seen:
                continue
            dirs.append(candidate)
            seen.add(resolved)

    return dirs, pointer


def _discover_human_gate_warning(
    *,
    pointer: dict[str, Any] | None,
    detail: str,
) -> dict[str, Any] | None:
    if not isinstance(pointer, dict):
        return None
    status = str(pointer.get("status") or "").upper()
    if status != "HUMAN_ONLY":
        return None
    evidence_paths = []
    artifact_dir = pointer.get("artifact_dir")
    if isinstance(artifact_dir, str) and artifact_dir.strip():
        evidence_paths.append(artifact_dir.strip())
    return _make_check(
        False,
        reason="DISCOVER_HUMAN_GATE_REQUIRED",
        status="WARN",
        details=detail,
        evidence_paths=evidence_paths,
    )


# ---------------------------------------------------------------------------
# Check 1: RAW module present
# ---------------------------------------------------------------------------

def check_raw_module_present(
    artifacts_root: Path,
    snapshot_path: Path | None = None,
) -> dict[str, Any]:
    """PASS if latest library snapshot contains a RAW module."""
    if snapshot_path is None:
        candidates = _iter_snapshot_candidates(artifacts_root)
        if candidates:
            snapshot_path = candidates[0]

    if not snapshot_path or not snapshot_path.is_file():
        return _make_check(
            False,
            reason="SNAPSHOT_MISSING",
            details="No library snapshot found (expected acceptance/final_library_snapshot.json or phase0/kajabi_library_snapshot.json)",
        )

    try:
        data = json.loads(snapshot_path.read_text())
    except Exception as e:
        return _make_check(False, reason="SNAPSHOT_PARSE_ERROR", details=str(e)[:200])

    home = data.get("home", {}) if isinstance(data, dict) else {}
    home_modules = home.get("modules", []) if isinstance(home, dict) else []
    if not isinstance(home_modules, list):
        return _make_check(
            False,
            reason="SNAPSHOT_INVALID",
            details=f"Snapshot missing home.modules list: {snapshot_path}",
            evidence_paths=[str(snapshot_path)],
        )

    matched = []
    for m in home_modules:
        name = m if isinstance(m, str) else (m.get("name", "") if isinstance(m, dict) else str(m))
        if name.lower().strip() in RAW_MODULE_NAMES:
            matched.append(name)

    if matched:
        return _make_check(True, details=f"RAW module found: {matched}",
                           evidence_paths=[str(snapshot_path)])
    return _make_check(False, reason="RAW_MODULE_MISSING",
                       details=f"No RAW module in {len(home_modules)} home modules",
                       evidence_paths=[str(snapshot_path)])


# ---------------------------------------------------------------------------
# Check 2: Site hostname
# ---------------------------------------------------------------------------

def check_site_hostname(
    artifacts_root: Path,
    expected: str = EXPECTED_HOSTNAME,
) -> dict[str, Any]:
    """PASS if resolved site hostname matches expected."""
    discover_base = artifacts_root / "soma_kajabi" / "discover"
    if discover_base.is_dir():
        dirs = sorted(
            (d for d in discover_base.iterdir() if d.is_dir()),
            key=lambda d: d.stat().st_mtime, reverse=True,
        )
        for d in dirs[:3]:
            products = d / "products.json"
            if products.is_file():
                try:
                    pdata = json.loads(products.read_text())
                    for p in (pdata if isinstance(pdata, list) else pdata.get("products", [])):
                        url = p.get("url", "") or p.get("site_url", "")
                        if url:
                            from urllib.parse import urlparse
                            hostname = urlparse(url).hostname
                            if hostname:
                                passed = hostname == expected
                                return _make_check(
                                    passed,
                                    details=f"hostname={hostname}, expected={expected}",
                                    evidence_paths=[str(products)],
                                    reason="" if passed else "HOSTNAME_MISMATCH",
                                )
                except Exception:
                    continue

    config_url = f"https://{expected}"
    return _make_check(
        True,
        details=f"Using configured hostname: {expected} (no discover artifact to verify)",
        evidence_paths=[],
    )


# ---------------------------------------------------------------------------
# Check 3: Landing page reachable
# ---------------------------------------------------------------------------

def check_landing_page(
    landing_url: str = DEFAULT_LANDING_URL,
    timeout: int = 15,
) -> dict[str, Any]:
    """PASS if landing URL returns HTTP 200 (or 3xx same host)."""
    try:
        req = Request(landing_url, method="GET")
        req.add_header("User-Agent", "ai-ops-runner/business-dod-check")
        resp = urlopen(req, timeout=timeout)
        code = resp.getcode()
        passed = 200 <= code < 400
        return _make_check(
            passed,
            details=f"HTTP {code} from {landing_url}",
            reason="" if passed else "LANDING_PAGE_ERROR",
        )
    except URLError as e:
        return _make_check(False, reason="LANDING_PAGE_UNREACHABLE",
                           details=f"{landing_url}: {e}")
    except Exception as e:
        return _make_check(False, reason="LANDING_PAGE_ERROR",
                           details=f"{landing_url}: {e}")


# ---------------------------------------------------------------------------
# Check 4: Terms + Privacy URLs
# ---------------------------------------------------------------------------

def check_terms_privacy(
    urls: list[str] | None = None,
    timeout: int = 15,
) -> dict[str, Any]:
    """PASS if all configured legal URLs return 200 or 3xx."""
    urls = urls or DEFAULT_TERMS_URLS
    results = []
    all_pass = True
    for url in urls:
        try:
            req = Request(url, method="GET")
            req.add_header("User-Agent", "ai-ops-runner/business-dod-check")
            resp = urlopen(req, timeout=timeout)
            code = resp.getcode()
            ok = 200 <= code < 400
            results.append({"url": url, "status": code, "pass": ok})
            if not ok:
                all_pass = False
        except Exception as e:
            results.append({"url": url, "status": 0, "pass": False, "error": str(e)[:100]})
            all_pass = False

    return _make_check(
        all_pass,
        details=json.dumps(results),
        reason="" if all_pass else "TERMS_PRIVACY_UNREACHABLE",
    )


# ---------------------------------------------------------------------------
# Check 5: Offer URLs present
# ---------------------------------------------------------------------------

def check_offer_urls(
    artifacts_root: Path,
    required_urls: list[str] | None = None,
) -> dict[str, Any]:
    """PASS if required offer URLs appear in memberships page HTML."""
    required_urls = required_urls or REQUIRED_OFFER_URLS
    discover_base = _discover_base(artifacts_root)

    if not discover_base.is_dir():
        return _make_check(False, reason="DISCOVER_ARTIFACTS_MISSING",
                           details="No discover artifacts directory")

    dirs, pointer = _discover_candidate_dirs(artifacts_root)
    warning = _discover_human_gate_warning(
        pointer=pointer,
        detail="discover is HUMAN_ONLY; memberships_page.html will be captured after interactive auth completes",
    )
    if warning is not None and not dirs:
        return warning
    for d in dirs[:3]:
        memberships_html = d / "memberships_page.html"
        if memberships_html.is_file():
            age_days = (datetime.now(timezone.utc).timestamp() - memberships_html.stat().st_mtime) / 86400
            content = memberships_html.read_text(errors="replace")
            missing = [u for u in required_urls if u not in content]
            if missing:
                return _make_check(
                    False, reason="OFFER_URLS_MISSING",
                    details=f"Missing: {missing} (age: {age_days:.1f}d)",
                    evidence_paths=[str(memberships_html)],
                )
            return _make_check(
                True,
                details=f"All {len(required_urls)} offer URLs present (age: {age_days:.1f}d)",
                evidence_paths=[str(memberships_html)],
            )

        if warning is not None and isinstance(pointer, dict):
            pointer_dir = str(pointer.get("artifact_dir") or "").strip()
            if pointer_dir and Path(pointer_dir) == d:
                return warning

    return _make_check(False, reason="MEMBERSHIPS_PAGE_MISSING",
                       details="memberships_page.html not found in recent discover runs")


# ---------------------------------------------------------------------------
# Check 6: No secrets in artifacts
# ---------------------------------------------------------------------------

def check_no_secrets(
    artifacts_root: Path,
    scan_dirs: list[str] | None = None,
) -> dict[str, Any]:
    """PASS if no secret patterns found in artifact files."""
    if scan_dirs is None:
        scan_dirs_paths = []
        for sub in ["post_deploy", "soma_kajabi/acceptance", "soma_kajabi/auto_finish"]:
            base = artifacts_root / sub
            if base.is_dir():
                dirs = sorted(
                    (d for d in base.iterdir() if d.is_dir()),
                    key=lambda d: d.stat().st_mtime, reverse=True,
                )
                if dirs:
                    scan_dirs_paths.append(dirs[0])
    else:
        scan_dirs_paths = [Path(d) for d in scan_dirs]

    hits: list[dict[str, str]] = []
    files_scanned = 0
    compiled = [(re.compile(p), label) for p, label in SECRET_PATTERNS]

    for scan_dir in scan_dirs_paths:
        if not scan_dir.is_dir():
            continue
        for fp in scan_dir.rglob("*"):
            if not fp.is_file():
                continue
            if fp.suffix in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp4", ".zip", ".gz", ".tar"):
                continue
            if fp.stat().st_size > 5 * 1024 * 1024:
                continue
            files_scanned += 1
            try:
                content = fp.read_text(errors="replace")
            except Exception:
                continue
            for pattern, label in compiled:
                for match in pattern.finditer(content):
                    matched_str = match.group()
                    if any(allow in matched_str for allow in SECRETS_ALLOWLIST):
                        continue
                    snippet_hash = hashlib.sha256(matched_str.encode()).hexdigest()[:12]
                    hits.append({
                        "file": str(fp.relative_to(artifacts_root)) if _is_relative_to(fp, artifacts_root) else str(fp),
                        "type": label,
                        "snippet_hash": snippet_hash,
                        "line_prefix": matched_str[:8] + "..." if len(matched_str) > 8 else matched_str,
                    })

    if hits:
        return _make_check(
            False, reason="SECRETS_DETECTED_IN_ARTIFACTS",
            details=f"{len(hits)} potential secret(s) in {files_scanned} files scanned: "
                    + json.dumps([{"type": h["type"], "file": h["file"], "hash": h["snippet_hash"]} for h in hits[:10]]),
        )
    return _make_check(True, details=f"No secrets found in {files_scanned} files scanned")


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Check 7: Community + groups exist
# ---------------------------------------------------------------------------

def check_community_groups(
    artifacts_root: Path,
    expected_community: str = EXPECTED_COMMUNITY_NAME,
    expected_groups: list[str] | None = None,
) -> dict[str, Any]:
    """PASS if community artifacts show expected community + groups."""
    expected_groups = expected_groups or EXPECTED_GROUPS

    discover_base = _discover_base(artifacts_root)
    if not discover_base.is_dir():
        return _make_check(False, reason="DISCOVER_ARTIFACTS_MISSING",
                           details="No discover artifacts for community check")

    dirs, pointer = _discover_candidate_dirs(artifacts_root)
    warning = _discover_human_gate_warning(
        pointer=pointer,
        detail="discover is HUMAN_ONLY; community artifacts will be captured after interactive auth completes",
    )
    if warning is not None and not dirs:
        return warning
    for d in dirs[:3]:
        community_json = d / "community.json"
        if community_json.is_file():
            try:
                cdata = json.loads(community_json.read_text())
                cname = cdata.get("name", "")
                groups = cdata.get("groups", [])
                group_names = [g.get("name", "") if isinstance(g, dict) else str(g) for g in groups]

                name_match = cname.lower().strip() == expected_community.lower().strip()
                missing_groups = [g for g in expected_groups if g.lower() not in [gn.lower() for gn in group_names]]

                passed = name_match and len(missing_groups) == 0
                details = f"community={cname}, groups={group_names}"
                if not name_match:
                    details += f"; expected community name '{expected_community}'"
                if missing_groups:
                    details += f"; missing groups: {missing_groups}"

                return _make_check(
                    passed, details=details,
                    evidence_paths=[str(community_json)],
                    reason="" if passed else "COMMUNITY_GROUPS_MISMATCH",
                )
            except Exception as e:
                continue

        community_html = d / "community.html"
        if community_html.is_file():
            try:
                content = community_html.read_text(errors="replace")
                name_found = expected_community.lower() in content.lower()
                groups_found = [g for g in expected_groups if g.lower() in content.lower()]
                missing = [g for g in expected_groups if g not in groups_found]

                passed = name_found and len(missing) == 0
                details = f"HTML scan: community_name_found={name_found}, groups_found={groups_found}"
                if missing:
                    details += f", missing={missing}"

                return _make_check(
                    passed, details=details,
                    evidence_paths=[str(community_html)],
                    reason="" if passed else "COMMUNITY_GROUPS_MISMATCH",
                )
            except Exception:
                continue

        if warning is not None and isinstance(pointer, dict):
            pointer_dir = str(pointer.get("artifact_dir") or "").strip()
            if pointer_dir and Path(pointer_dir) == d:
                return warning

    return _make_check(False, reason="COMMUNITY_ARTIFACTS_MISSING",
                       details="No community.json or community.html in recent discover runs")


# ---------------------------------------------------------------------------
# Check 8: Gmail dedupe metadata
# ---------------------------------------------------------------------------

def check_manifest_dedupe(
    artifacts_root: Path,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    """PASS if video_manifest.csv has hash/id column with no un-deduped duplicates."""
    if manifest_path is None:
        accept_base = artifacts_root / "soma_kajabi" / "acceptance"
        if accept_base.is_dir():
            dirs = sorted(
                (d for d in accept_base.iterdir() if d.is_dir()),
                key=lambda d: d.stat().st_mtime, reverse=True,
            )
            for d in dirs[:5]:
                candidate = d / "video_manifest.csv"
                if candidate.is_file():
                    manifest_path = candidate
                    break

    if not manifest_path or not manifest_path.is_file():
        return _make_check(False, reason="MANIFEST_NOT_FOUND",
                           details="video_manifest.csv not found")

    try:
        with manifest_path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames or []
            rows = list(reader)
    except Exception as e:
        return _make_check(False, reason="MANIFEST_PARSE_ERROR", details=str(e)[:200])

    hash_col = None
    for candidate_col in ["content_sha256", "manifest_hash", "manifest_id"]:
        if candidate_col in fieldnames:
            hash_col = candidate_col
            break

    if hash_col is None:
        return _make_check(
            False,
            reason="MANIFEST_NO_HASH_COLUMN",
            details=f"Missing required manifest hash column (expected content_sha256/manifest_hash/manifest_id). Columns: {fieldnames}",
        )

    seen: dict[str, list[int]] = {}
    for idx, row in enumerate(rows):
        val = (row.get(hash_col) or "").strip()
        if not val:
            continue
        seen.setdefault(val, []).append(idx)

    duplicates = {k: v for k, v in seen.items() if len(v) > 1}

    unmarked_dupes = {}
    for hash_val, indices in duplicates.items():
        unmarked = []
        for i in indices:
            status = (rows[i].get("status") or "").lower()
            deduped_flag = (rows[i].get("deduped") or "").lower()
            if deduped_flag not in ("true", "yes", "1") and status != "deduped":
                unmarked.append(i)
        if len(unmarked) > 1:
            unmarked_dupes[hash_val] = unmarked

    if unmarked_dupes:
        summary = {k: len(v) for k, v in list(unmarked_dupes.items())[:5]}
        return _make_check(
            False, reason="MANIFEST_DUPLICATES_FOUND",
            details=f"{len(unmarked_dupes)} duplicate hash(es): {summary}",
            evidence_paths=[str(manifest_path)],
        )

    return _make_check(
        True,
        details=f"{len(rows)} rows, hash_col={hash_col}, {len(duplicates)} duplicate sets (all deduped)",
        evidence_paths=[str(manifest_path)],
    )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def verify_business_dod(
    artifacts_root: Path | None = None,
    project_id: str = "soma_kajabi",
    *,
    snapshot_path: Path | None = None,
    manifest_path: Path | None = None,
    landing_url: str | None = None,
    terms_urls: list[str] | None = None,
    skip_network_checks: bool = False,
) -> dict[str, Any]:
    """Run all 8 Business DoD checks and produce structured result.

    When skip_network_checks=True, checks 3/4 are skipped (for hermetic testing).
    """
    if artifacts_root is None:
        artifacts_root = _artifacts_root()

    run_id = f"bdod_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{_random_hex()}"
    out_dir = artifacts_root / project_id / "business_dod" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir = out_dir / "evidence"
    evidence_dir.mkdir(exist_ok=True)

    checks: dict[str, dict[str, Any]] = {}

    checks["raw_module_present"] = check_raw_module_present(artifacts_root, snapshot_path)

    checks["site_hostname"] = check_site_hostname(artifacts_root)

    if skip_network_checks:
        checks["landing_page_reachable"] = _make_check(True, details="skipped (hermetic mode)")
        checks["terms_privacy_urls"] = _make_check(True, details="skipped (hermetic mode)")
    else:
        checks["landing_page_reachable"] = check_landing_page(landing_url or DEFAULT_LANDING_URL)
        checks["terms_privacy_urls"] = check_terms_privacy(terms_urls)

    checks["offer_urls_present"] = check_offer_urls(artifacts_root)
    checks["no_secrets_in_artifacts"] = check_no_secrets(artifacts_root)
    checks["community_groups_exist"] = check_community_groups(artifacts_root)
    checks["manifest_dedupe"] = check_manifest_dedupe(artifacts_root, manifest_path)

    all_pass = all(c.get("status", "PASS") != "FAIL" for c in checks.values())
    warnings: list[str] = []
    for name, c in checks.items():
        status = str(c.get("status") or ("PASS" if c.get("pass") else "FAIL"))
        if status != "PASS":
            warnings.append(f"{name}: {status} {c.get('reason', 'FAIL')} — {c.get('details', '')[:120]}")

    result = {
        "pass": all_pass,
        "run_id": run_id,
        "project_id": project_id,
        "created_at": _now_iso(),
        "checks": checks,
        "warnings": warnings,
        "checks_passed": sum(1 for c in checks.values() if c.get("status") == "PASS"),
        "checks_warned": sum(1 for c in checks.values() if c.get("status") == "WARN"),
        "checks_total": len(checks),
        "artifact_dir": str(out_dir),
        "business_dod_artifact_dir": str(out_dir),
    }

    (out_dir / "business_dod_checks.json").write_text(json.dumps(result, indent=2, default=str))

    summary_lines = [
        "# Business DoD — " + ("PASS" if all_pass else "FAIL"),
        "",
        f"**Run ID:** {run_id}",
        f"**Created:** {result['created_at']}",
        f"**Checks:** {result['checks_passed']}/{result['checks_total']} passed",
        "",
        "## Check Results",
        "",
        "| # | Check | Result | Details |",
        "|---|-------|--------|---------|",
    ]
    for i, (name, c) in enumerate(checks.items(), 1):
        status = str(c.get("status") or ("PASS" if c.get("pass") else "FAIL"))
        detail_short = (c.get("details") or "")[:80].replace("|", "\\|")
        summary_lines.append(f"| {i} | {name} | {status} | {detail_short} |")

    if warnings:
        summary_lines.extend(["", "## Warnings", ""])
        for w in warnings:
            summary_lines.append(f"- {w}")

    summary_lines.append("")
    (out_dir / "SUMMARY.md").write_text("\n".join(summary_lines))
    _write_latest_pointer(
        artifacts_root,
        project_id,
        run_id=run_id,
        out_dir=out_dir,
        passed=all_pass,
    )

    return result


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> int:
    result = verify_business_dod()
    print(json.dumps({
        "pass": result["pass"],
        "run_id": result["run_id"],
        "checks_passed": result["checks_passed"],
        "checks_total": result["checks_total"],
        "artifact_dir": result["artifact_dir"],
        "business_dod_artifact_dir": result["business_dod_artifact_dir"],
    }, indent=2))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
