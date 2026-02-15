#!/usr/bin/env python3
"""mirror_home_to_practitioner — Mirror Home Library → Practitioner Library.

Usage:
    python -m soma_kajabi_sync.mirror
    python -m soma_kajabi_sync.mirror --smoke  # smoke test
    python -m soma_kajabi_sync.mirror --dry-run  # show what would change

Produces:
    artifacts/soma/<run_id>/mirror_report.json
    artifacts/soma/<run_id>/changelog.md

Compares the two most recent snapshots (Home + Practitioner) and produces
a diff-based action plan. In --dry-run mode, no mutations are applied.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .artifacts import (
    write_changelog,
    write_mirror_report,
    write_run_manifest,
)
from .config import ARTIFACTS_ROOT, get_artifacts_dir


def _generate_run_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    short = uuid.uuid4().hex[:8]
    return f"mirror_{ts}_{short}"


def _find_latest_snapshot(product_filter: str) -> dict[str, Any] | None:
    """Find the most recent snapshot.json for a given product."""
    if not ARTIFACTS_ROOT.exists():
        return None

    candidates: list[tuple[Path, float]] = []
    for run_dir in ARTIFACTS_ROOT.iterdir():
        if not run_dir.is_dir():
            continue
        snap = run_dir / "snapshot.json"
        if snap.exists():
            try:
                data = json.loads(snap.read_text())
                if data.get("product") == product_filter:
                    candidates.append((snap, snap.stat().st_mtime))
            except (json.JSONDecodeError, OSError):
                continue

    if not candidates:
        return None

    # Most recent by mtime
    candidates.sort(key=lambda x: x[1], reverse=True)
    return json.loads(candidates[0][0].read_text())


def _diff_snapshots(
    home: dict[str, Any], practitioner: dict[str, Any]
) -> list[dict[str, str]]:
    """Compute actions needed to mirror Home → Practitioner.

    Returns a list of action dicts:
    - {action: "add_category", name: ..., detail: ...}
    - {action: "add_item", title: ..., category: ..., detail: ...}
    - {action: "update_item", title: ..., category: ..., detail: ...}
    - {action: "reorder", title: ..., category: ..., detail: ...}
    """
    actions: list[dict[str, str]] = []

    # Build lookup for practitioner categories
    pract_cats: dict[str, dict[str, Any]] = {}
    for cat in practitioner.get("categories", []):
        pract_cats[cat["name"]] = cat

    for home_cat in home.get("categories", []):
        cat_name = home_cat["name"]

        if cat_name not in pract_cats:
            actions.append(
                {
                    "action": "add_category",
                    "title": cat_name,
                    "detail": f"Category '{cat_name}' exists in Home but not Practitioner",
                }
            )
            # All items in this category need to be added
            for item in home_cat.get("items", []):
                actions.append(
                    {
                        "action": "add_item",
                        "title": item["title"],
                        "category": cat_name,
                        "detail": f"New item in new category '{cat_name}'",
                    }
                )
            continue

        # Category exists — compare items
        pract_items: dict[str, dict[str, Any]] = {}
        for item in pract_cats[cat_name].get("items", []):
            pract_items[item["title"]] = item

        for home_item in home_cat.get("items", []):
            item_title = home_item["title"]
            if item_title not in pract_items:
                actions.append(
                    {
                        "action": "add_item",
                        "title": item_title,
                        "category": cat_name,
                        "detail": f"Item exists in Home/{cat_name} but not Practitioner",
                    }
                )
            else:
                # Check for position differences
                pract_item = pract_items[item_title]
                if home_item.get("position") != pract_item.get("position"):
                    actions.append(
                        {
                            "action": "reorder",
                            "title": item_title,
                            "category": cat_name,
                            "detail": (
                                f"Position mismatch: Home={home_item.get('position')} "
                                f"vs Practitioner={pract_item.get('position')}"
                            ),
                        }
                    )

    return actions


def mirror_home_to_practitioner(
    smoke: bool = False, dry_run: bool = False
) -> dict[str, Any]:
    """Main entrypoint: mirror Home Library → Practitioner Library.

    Returns a result dict with status and artifact paths.
    """
    run_id = _generate_run_id()
    out_dir = get_artifacts_dir(run_id)

    print("=== mirror_home_to_practitioner ===")
    print(f"  Run ID:   {run_id}")
    print(f"  Out dir:  {out_dir}")
    print(f"  Dry run:  {dry_run}")
    print()

    if smoke:
        # Smoke test: synthetic diff
        print("  [SMOKE MODE] Using synthetic snapshot data")
        home_snapshot = {
            "product": "Home User Library",
            "categories": [
                {
                    "name": "Module 1",
                    "items": [
                        {"title": "Intro Video", "position": 0},
                        {"title": "Deep Dive", "position": 1},
                        {"title": "New Content", "position": 2},
                    ],
                },
                {
                    "name": "Module 2",
                    "items": [
                        {"title": "Overview", "position": 0},
                    ],
                },
            ],
        }
        pract_snapshot = {
            "product": "Practitioner Library",
            "categories": [
                {
                    "name": "Module 1",
                    "items": [
                        {"title": "Intro Video", "position": 0},
                        {"title": "Deep Dive", "position": 1},
                    ],
                },
            ],
        }
    else:
        # Load latest snapshots
        print("  Loading latest Home User Library snapshot...")
        home_snapshot = _find_latest_snapshot("Home User Library")
        if not home_snapshot:
            print(
                "ERROR: No Home User Library snapshot found. "
                "Run snapshot_kajabi first.",
                file=sys.stderr,
            )
            write_run_manifest(
                out_dir, run_id, "mirror_home_to_practitioner", "error",
                [], error="No Home snapshot found",
            )
            sys.exit(1)

        print("  Loading latest Practitioner Library snapshot...")
        pract_snapshot = _find_latest_snapshot("Practitioner Library")
        if not pract_snapshot:
            print(
                "ERROR: No Practitioner Library snapshot found. "
                "Run snapshot_kajabi first.",
                file=sys.stderr,
            )
            write_run_manifest(
                out_dir, run_id, "mirror_home_to_practitioner", "error",
                [], error="No Practitioner snapshot found",
            )
            sys.exit(1)

    # Compute diff
    actions = _diff_snapshots(home_snapshot, pract_snapshot)

    summary = {
        "total_actions": len(actions),
        "add_category": sum(1 for a in actions if a["action"] == "add_category"),
        "add_item": sum(1 for a in actions if a["action"] == "add_item"),
        "update_item": sum(1 for a in actions if a["action"] == "update_item"),
        "reorder": sum(1 for a in actions if a["action"] == "reorder"),
    }

    print(f"  Actions computed: {summary['total_actions']}")
    for key, count in summary.items():
        if key != "total_actions" and count > 0:
            print(f"    {key}: {count}")

    if dry_run:
        print("\n  [DRY RUN] No mutations applied.")
    elif not smoke and actions:
        # TODO: Apply mutations via Kajabi API or Playwright
        # For now, we only produce the report — actual mutations require
        # Kajabi API write access or Playwright automation.
        print("\n  NOTE: Mutation application not yet implemented.")
        print("  Review mirror_report.json and apply changes manually,")
        print("  or implement Kajabi write automation in a future version.")

    # Write artifacts
    report_path = write_mirror_report(
        out_dir,
        source_product="Home User Library",
        target_product="Practitioner Library",
        actions=actions,
        summary=summary,
    )
    changelog_path = write_changelog(
        out_dir,
        entries=actions,
    )

    print(f"\n  Report:    {report_path}")
    print(f"  Changelog: {changelog_path}")

    artifacts_written = [
        "mirror_report.json",
        "mirror_report.json.sha256",
        "changelog.md",
    ]
    status = "success" if not actions or dry_run else "needs_review"
    write_run_manifest(
        out_dir, run_id, "mirror_home_to_practitioner", status, artifacts_written
    )

    result = {
        "status": status,
        "run_id": run_id,
        "artifacts_dir": str(out_dir),
        "artifacts": artifacts_written,
        "summary": summary,
        "dry_run": dry_run,
    }
    print(f"\n  Result: {json.dumps(result, indent=2)}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Mirror Home Library → Practitioner Library"
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="Smoke test mode — synthetic data, no credentials needed",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without applying mutations",
    )
    args = parser.parse_args()
    mirror_home_to_practitioner(smoke=args.smoke, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
