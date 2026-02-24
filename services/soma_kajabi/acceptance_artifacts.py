"""Soma acceptance artifacts — Final Library Snapshot, Video Manifest, Mirror Report, Changelog.

Writes under artifacts/soma_kajabi/acceptance/<run_id>/.
No secrets. Used by soma_kajabi_auto_finish.
"""

from __future__ import annotations

import csv
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_above_paywall(lesson: dict) -> bool:
    """True if lesson is above paywall (or unknown — treat as above for safety)."""
    v = (lesson.get("above_paywall") or "").lower()
    if v in ("no", "false", "0", "below"):
        return False
    return True


def _lesson_key(lesson: dict) -> tuple[str, str]:
    """(module_name, title) for dedup/comparison."""
    return (lesson.get("module_name", ""), lesson.get("title", ""))


def _compute_mirror_exceptions(snapshot: dict) -> list[dict]:
    """Home above-paywall lessons missing or mismatched in Practitioner. Empty = PASS."""
    home = snapshot.get("home", {})
    pract = snapshot.get("practitioner", {})
    home_lessons = home.get("lessons", [])
    pract_lessons = pract.get("lessons", [])

    pract_lookup: dict[tuple[str, str], dict] = {}
    for p in pract_lessons:
        pract_lookup[_lesson_key(p)] = p

    exceptions: list[dict] = []
    for h in home_lessons:
        if not _is_above_paywall(h):
            continue
        key = _lesson_key(h)
        if key not in pract_lookup:
            exceptions.append({
                "module": h.get("module_name", ""),
                "title": h.get("title", ""),
                "reason": "missing_in_practitioner",
                "home_lesson": h,
            })
        else:
            p = pract_lookup[key]
            # Optional: check video match only when both are non-empty and differ
            hv = h.get("attached_video_name") or ""
            pv = p.get("attached_video_name") or ""
            if hv and pv and hv != pv:
                exceptions.append({
                    "module": h.get("module_name", ""),
                    "title": h.get("title", ""),
                    "reason": "video_mismatch",
                    "home_video": hv,
                    "pract_video": pv,
                })
    return exceptions


def _load_video_manifest(path: Path) -> list[dict]:
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


def _write_final_library_snapshot(accept_dir: Path, snapshot: dict) -> Path:
    """Final Library Snapshot: Home + Practitioner full trees."""
    doc = {
        "schema_version": 1,
        "generated_at": _now_iso(),
        "home": snapshot.get("home", {}),
        "practitioner": snapshot.get("practitioner", {}),
    }
    path = accept_dir / "final_library_snapshot.json"
    path.write_text(json.dumps(doc, indent=2))
    return path


def _write_video_manifest_artifact(accept_dir: Path, manifest_rows: list[dict], phase0_dir: Path) -> Path:
    """Video Manifest: one row per Zane email video. Copy/transform from Phase0."""
    src = phase0_dir / "video_manifest.csv"
    if src.exists():
        shutil.copy(src, accept_dir / "video_manifest.csv")
        return accept_dir / "video_manifest.csv"
    # Write from rows
    fieldnames = [
        "email_id", "subject", "file_name", "sha256", "rough_topic",
        "proposed_module", "proposed_lesson_title", "proposed_description", "status",
    ]
    path = accept_dir / "video_manifest.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(manifest_rows)
    return path


def _write_mirror_report(accept_dir: Path, exceptions: list[dict], snapshot: dict) -> Path:
    """Mirror Report: Home → Practitioner. exceptions empty = PASS."""
    doc = {
        "schema_version": 1,
        "generated_at": _now_iso(),
        "source": "Home User Library",
        "target": "Practitioner Library",
        "pass": len(exceptions) == 0,
        "exceptions": exceptions,
        "exceptions_count": len(exceptions),
    }
    path = accept_dir / "mirror_report.json"
    path.write_text(json.dumps(doc, indent=2))
    return path


def _write_changelog(
    accept_dir: Path,
    snapshot: dict,
    manifest_rows: list[dict],
    exceptions: list[dict],
) -> Path:
    """Changelog: lessons created/updated, videos attached, moves to RAW, open questions."""
    lines = [
        "# Soma Acceptance Changelog",
        "",
        f"Generated: {_now_iso()}",
        "",
        "## Summary",
        "",
        f"- Home modules: {len(snapshot.get('home', {}).get('modules', []))}",
        f"- Home lessons: {len(snapshot.get('home', {}).get('lessons', []))}",
        f"- Practitioner lessons: {len(snapshot.get('practitioner', {}).get('lessons', []))}",
        f"- Video manifest rows: {len(manifest_rows)}",
        f"- Mirror exceptions: {len(exceptions)}",
        "",
        "## Lessons",
        "",
    ]
    home_lessons = snapshot.get("home", {}).get("lessons", [])
    for L in home_lessons[:50]:  # Cap for readability
        mod = L.get("module_name", "")
        title = L.get("title", "")
        pub = L.get("published_state", "")
        vid = L.get("attached_video_name", "")
        lines.append(f"- [{mod}] {title} ({pub}) video={vid or 'none'}")
    if len(home_lessons) > 50:
        lines.append(f"- ... and {len(home_lessons) - 50} more")
    lines.append("")
    lines.append("## Videos")
    lines.append("")
    raw_count = sum(1 for r in manifest_rows if (r.get("status") or "").lower() in ("raw_needs_review", "unmapped"))
    attached_count = sum(1 for r in manifest_rows if (r.get("status") or "").lower() in ("attached", "mapped"))
    lines.append(f"- Attached/mapped: {attached_count}")
    lines.append(f"- Raw needs review: {raw_count}")
    lines.append("")
    lines.append("## Open Questions")
    lines.append("")
    if exceptions:
        for e in exceptions:
            lines.append(f"- Mirror: {e.get('module', '')} / {e.get('title', '')} — {e.get('reason', '')}")
    else:
        lines.append("- (none)")
    path = accept_dir / "changelog.md"
    path.write_text("\n".join(lines))
    return path


def write_acceptance_artifacts(
    root: Path,
    run_id: str,
    phase0_dir: Path,
) -> tuple[Path, dict]:
    """Write all four required artifacts under artifacts/soma_kajabi/acceptance/<run_id>/.

    Returns (accept_dir, summary_dict).
    summary_dict includes: pass, exceptions_count, artifact_paths.
    """
    accept_dir = root / "artifacts" / "soma_kajabi" / "acceptance" / run_id
    accept_dir.mkdir(parents=True, exist_ok=True)

    snap_path = phase0_dir / "kajabi_library_snapshot.json"
    if not snap_path.exists():
        snapshot = {"home": {"modules": [], "lessons": []}, "practitioner": {"modules": [], "lessons": []}}
    else:
        snapshot = json.loads(snap_path.read_text())

    manifest_rows = _load_video_manifest(phase0_dir / "video_manifest.csv")
    exceptions = _compute_mirror_exceptions(snapshot)

    _write_final_library_snapshot(accept_dir, snapshot)
    _write_video_manifest_artifact(accept_dir, manifest_rows, phase0_dir)
    _write_mirror_report(accept_dir, exceptions, snapshot)
    _write_changelog(accept_dir, snapshot, manifest_rows, exceptions)

    summary = {
        "pass": len(exceptions) == 0,
        "exceptions_count": len(exceptions),
        "artifact_paths": [
            "final_library_snapshot.json",
            "video_manifest.csv",
            "mirror_report.json",
            "changelog.md",
        ],
    }
    # Write acceptance_summary.json for HQ badge (no secrets)
    (accept_dir / "acceptance_summary.json").write_text(
        json.dumps({"pass": summary["pass"], "run_id": run_id, "generated_at": _now_iso()}, indent=2)
    )
    return accept_dir, summary
