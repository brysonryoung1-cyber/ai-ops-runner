#!/usr/bin/env python3
"""soma_zane_finish_plan — Read-only punchlist from Phase0 artifacts.

Finds latest Phase0 run (or RUN_ID env), reads kajabi_library_snapshot.json +
video_manifest.csv, produces prioritized PUNCHLIST.md, PUNCHLIST.csv, SUMMARY.json.

Categories: A) Library/mirroring, B) Offers/checkout, C) Landing/nav/branding,
D) Email/onboarding (Gmail-dependent = BLOCKED), E) QA purchase flow.
First 3 of next 10 actions are purely Kajabi UI tasks.
"""

from __future__ import annotations

import csv
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ARTIFACTS_ROOT = Path("artifacts/soma_kajabi/zane_finish_plan")
PHASE0_ROOT = Path("artifacts/soma_kajabi/phase0")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _generate_run_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    short = uuid.uuid4().hex[:8]
    return f"zane_{ts}_{short}"


def _repo_root() -> Path:
    env_root = os.environ.get("OPENCLAW_REPO_ROOT")
    if env_root and Path(env_root).exists():
        return Path(env_root)
    cwd = Path.cwd()
    for _ in range(10):
        if (cwd / "config" / "project_state.json").exists():
            return cwd
        if cwd == cwd.parent:
            break
        cwd = cwd.parent
    return Path(env_root or "/opt/ai-ops-runner")


def _find_latest_phase0_dir(root: Path) -> Path | None:
    run_id = os.environ.get("RUN_ID")
    if run_id:
        cand = root / PHASE0_ROOT / run_id
        if cand.exists() and (cand / "kajabi_library_snapshot.json").exists():
            return cand
        # Try without phase0_ prefix if run_id is short
        for d in (root / PHASE0_ROOT).iterdir():
            if d.is_dir() and d.name.endswith(run_id):
                return d
    phase0_base = root / PHASE0_ROOT
    if not phase0_base.exists():
        return None
    dirs = [d for d in phase0_base.iterdir() if d.is_dir()]
    if not dirs:
        return None
    # Prefer dirs with valid snapshot
    valid = [d for d in dirs if (d / "kajabi_library_snapshot.json").exists()]
    if not valid:
        return None
    return max(valid, key=lambda d: d.name)


def _load_snapshot(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _load_video_manifest(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    try:
        with path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(dict(row))
    except Exception:
        pass
    return rows


def _gmail_skipped(phase0_dir: Path) -> bool:
    harvest_path = phase0_dir / "gmail_harvest.jsonl"
    if not harvest_path.exists():
        return False
    try:
        first = harvest_path.read_text().strip().split("\n")[0]
        if not first:
            return True
        data = json.loads(first)
        return data.get("gmail_status") == "skipped"
    except Exception:
        return False


def _build_punchlist(snapshot: dict, manifest: list[dict], gmail_skipped: bool) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    home = snapshot.get("home", {})
    pract = snapshot.get("practitioner", {})
    home_modules = home.get("modules", [])
    home_lessons = home.get("lessons", [])
    pract_lessons = pract.get("lessons", [])

    # A) Library structure + mirroring completeness
    items.append({
        "id": "A1",
        "category": "A",
        "category_name": "Library structure + mirroring",
        "priority": "P0",
        "title": "Verify Home Library module structure",
        "description": f"Home has {len(home_modules)} modules, {len(home_lessons)} lessons",
        "blocked": False,
        "blocked_reason": None,
        "kajabi_ui": True,
    })
    items.append({
        "id": "A2",
        "category": "A",
        "category_name": "Library structure + mirroring",
        "priority": "P0",
        "title": "Verify Practitioner Library mirror completeness",
        "description": f"Practitioner has {len(pract_lessons)} lessons",
        "blocked": False,
        "blocked_reason": None,
        "kajabi_ui": True,
    })
    items.append({
        "id": "A3",
        "category": "A",
        "category_name": "Library structure + mirroring",
        "priority": "P1",
        "title": "Run mirror diff (Home → Practitioner)",
        "description": "Ensure all Home content is mirrored to Practitioner",
        "blocked": False,
        "blocked_reason": None,
        "kajabi_ui": True,
    })

    # B) Offers/checkout readiness
    items.append({
        "id": "B1",
        "category": "B",
        "category_name": "Offers/checkout readiness",
        "priority": "P0",
        "title": "Configure Kajabi Payments offer",
        "description": "Set up offer and checkout flow in Kajabi",
        "blocked": False,
        "blocked_reason": None,
        "kajabi_ui": True,
    })
    items.append({
        "id": "B2",
        "category": "B",
        "category_name": "Offers/checkout readiness",
        "priority": "P1",
        "title": "Verify checkout URL and pricing",
        "description": "Test offer link and price display",
        "blocked": False,
        "blocked_reason": None,
        "kajabi_ui": True,
    })

    # C) Landing pages/nav/branding
    items.append({
        "id": "C1",
        "category": "C",
        "category_name": "Landing pages/nav/branding",
        "priority": "P1",
        "title": "Review landing page copy and CTA",
        "description": "Polish landing page messaging",
        "blocked": False,
        "blocked_reason": None,
        "kajabi_ui": True,
    })
    items.append({
        "id": "C2",
        "category": "C",
        "category_name": "Landing pages/nav/branding",
        "priority": "P2",
        "title": "Nav and site structure",
        "description": "Ensure nav links and site structure are correct",
        "blocked": False,
        "blocked_reason": None,
        "kajabi_ui": True,
    })

    # D) Email sequences/onboarding (Gmail-dependent = BLOCKED when gmail skipped)
    items.append({
        "id": "D1",
        "category": "D",
        "category_name": "Email sequences/onboarding",
        "priority": "P1",
        "title": "Configure welcome email sequence",
        "description": "Set up post-purchase email sequence",
        "blocked": gmail_skipped,
        "blocked_reason": "Gmail OAuth not configured; run Gmail connect flow" if gmail_skipped else None,
        "kajabi_ui": False,
    })
    items.append({
        "id": "D2",
        "category": "D",
        "category_name": "Email sequences/onboarding",
        "priority": "P2",
        "title": "Map video manifest to lessons (Gmail harvest)",
        "description": f"Manifest has {len(manifest)} rows; map videos to Kajabi lessons",
        "blocked": gmail_skipped,
        "blocked_reason": "Gmail harvest skipped; video_manifest empty" if gmail_skipped else None,
        "kajabi_ui": False,
    })

    # E) QA purchase flow + member access
    items.append({
        "id": "E1",
        "category": "E",
        "category_name": "QA purchase flow + member access",
        "priority": "P0",
        "title": "End-to-end purchase flow test",
        "description": "Complete purchase as test user, verify member access",
        "blocked": False,
        "blocked_reason": None,
        "kajabi_ui": False,
    })
    items.append({
        "id": "E2",
        "category": "E",
        "category_name": "QA purchase flow + member access",
        "priority": "P1",
        "title": "Verify member dashboard and content access",
        "description": "Confirm members can access purchased content",
        "blocked": False,
        "blocked_reason": None,
        "kajabi_ui": False,
    })

    return items


def _next_10_actions(items: list[dict]) -> list[dict]:
    """First 3 must be purely Kajabi UI tasks."""
    kajabi_ui = [i for i in items if i.get("kajabi_ui") and not i.get("blocked")]
    other = [i for i in items if not i.get("kajabi_ui") or i.get("blocked")]
    # Sort by P0, P1, P2
    def _key(x):
        p = x.get("priority", "P2")
        return (0 if p == "P0" else 1 if p == "P1" else 2, x.get("id", ""))
    kajabi_ui.sort(key=_key)
    other.sort(key=_key)
    result = kajabi_ui[:3]  # First 3 = Kajabi UI only
    remaining = kajabi_ui[3:] + other
    remaining.sort(key=_key)
    result.extend(remaining[:7])
    return result[:10]


def main() -> int:
    root = _repo_root()
    phase0_dir = _find_latest_phase0_dir(root)
    if not phase0_dir:
        print(json.dumps({
            "ok": False,
            "error": "No Phase0 artifacts found; run soma_kajabi_phase0 first",
            "phase0_root": str(root / PHASE0_ROOT),
        }))
        return 1

    snapshot = _load_snapshot(phase0_dir / "kajabi_library_snapshot.json")
    manifest = _load_video_manifest(phase0_dir / "video_manifest.csv")
    gmail_skipped = _gmail_skipped(phase0_dir)

    run_id = _generate_run_id()
    out_dir = root / ARTIFACTS_ROOT / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    items = _build_punchlist(snapshot, manifest, gmail_skipped)
    next_10 = _next_10_actions(items)

    # PUNCHLIST.csv
    csv_path = out_dir / "PUNCHLIST.csv"
    fieldnames = ["id", "category", "priority", "title", "description", "blocked", "blocked_reason", "kajabi_ui"]
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(items)

    # PUNCHLIST.md
    md_lines = [
        "# Zane Finish Plan — Punchlist",
        "",
        f"Generated: {_now_iso()}",
        f"Phase0 source: {phase0_dir.relative_to(root) if root in phase0_dir.parents else phase0_dir}",
        f"Gmail skipped: {gmail_skipped}",
        "",
        "## Summary",
        "",
        f"- Home modules: {len(snapshot.get('home', {}).get('modules', []))}",
        f"- Home lessons: {len(snapshot.get('home', {}).get('lessons', []))}",
        f"- Practitioner lessons: {len(snapshot.get('practitioner', {}).get('lessons', []))}",
        f"- Video manifest rows: {len(manifest)}",
        "",
        "## Next 10 Actions (first 3 = Kajabi UI only)",
        "",
    ]
    for i, it in enumerate(next_10, 1):
        blk = " [BLOCKED]" if it.get("blocked") else ""
        md_lines.append(f"{i}. **{it.get('title', '')}** ({it.get('priority', '')}){blk}")
        if it.get("blocked_reason"):
            md_lines.append(f"   - {it['blocked_reason']}")
        md_lines.append("")
    md_lines.append("## Full Punchlist")
    md_lines.append("")
    for it in items:
        blk = " [BLOCKED]" if it.get("blocked") else ""
        md_lines.append(f"- [{it.get('id')}] {it.get('title', '')} ({it.get('priority', '')}){blk}")
    (out_dir / "PUNCHLIST.md").write_text("\n".join(md_lines), encoding="utf-8")

    # SUMMARY.json
    summary = {
        "ok": True,
        "run_id": run_id,
        "phase0_dir": str(phase0_dir.relative_to(root) if root in phase0_dir.parents else phase0_dir),
        "gmail_skipped": gmail_skipped,
        "artifact_paths": [
            str(ARTIFACTS_ROOT / run_id / "PUNCHLIST.md"),
            str(ARTIFACTS_ROOT / run_id / "PUNCHLIST.csv"),
            str(ARTIFACTS_ROOT / run_id / "SUMMARY.json"),
        ],
        "counts": {
            "home_modules": len(snapshot.get("home", {}).get("modules", [])),
            "home_lessons": len(snapshot.get("home", {}).get("lessons", [])),
            "practitioner_lessons": len(snapshot.get("practitioner", {}).get("lessons", [])),
            "video_manifest_rows": len(manifest),
        },
        "next_10_actions": [
            {
                "rank": i,
                "id": it.get("id"),
                "title": it.get("title"),
                "priority": it.get("priority"),
                "blocked": it.get("blocked"),
                "blocked_reason": it.get("blocked_reason"),
                "kajabi_ui": it.get("kajabi_ui"),
            }
            for i, it in enumerate(next_10, 1)
        ],
    }
    (out_dir / "SUMMARY.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
