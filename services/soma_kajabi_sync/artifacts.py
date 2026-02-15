"""Artifact management for Soma Kajabi Sync.

Writes structured artifacts under artifacts/soma/<run_id>/
with manifest + integrity metadata.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_str(data: str) -> str:
    return hashlib.sha256(data.encode()).hexdigest()


def write_snapshot_json(
    out_dir: Path,
    product_name: str,
    categories: list[dict[str, Any]],
) -> Path:
    """Write kajabi_library_snapshot.json (or snapshot.json)."""
    doc = {
        "schema_version": 1,
        "product": product_name,
        "captured_at": _now_iso(),
        "total_categories": len(categories),
        "total_items": sum(len(c.get("items", [])) for c in categories),
        "categories": categories,
    }
    data = json.dumps(doc, indent=2)
    path = out_dir / "snapshot.json"
    path.write_text(data)
    # Write integrity sidecar
    (out_dir / "snapshot.json.sha256").write_text(_sha256_str(data))
    return path


def write_video_manifest_csv(
    out_dir: Path,
    rows: list[dict[str, str]],
) -> Path:
    """Write video_manifest.csv with columns:
    video_id, title, source_email_id, date_received, status, kajabi_product,
    kajabi_category, file_url, notes
    """
    fieldnames = [
        "video_id",
        "title",
        "source_email_id",
        "date_received",
        "status",  # mapped | unmapped | raw_needs_review
        "kajabi_product",
        "kajabi_category",
        "file_url",
        "notes",
    ]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    data = buf.getvalue()
    path = out_dir / "video_manifest.csv"
    path.write_text(data)
    (out_dir / "video_manifest.csv.sha256").write_text(_sha256_str(data))
    return path


def write_gmail_video_index(
    out_dir: Path,
    emails: list[dict[str, Any]],
) -> Path:
    """Write gmail_video_index.json."""
    doc = {
        "schema_version": 1,
        "harvested_at": _now_iso(),
        "total_emails": len(emails),
        "emails": emails,
    }
    data = json.dumps(doc, indent=2)
    path = out_dir / "gmail_video_index.json"
    path.write_text(data)
    (out_dir / "gmail_video_index.json.sha256").write_text(_sha256_str(data))
    return path


def write_mirror_report(
    out_dir: Path,
    source_product: str,
    target_product: str,
    actions: list[dict[str, str]],
    summary: dict[str, int],
) -> Path:
    """Write mirror_report.json."""
    doc = {
        "schema_version": 1,
        "mirrored_at": _now_iso(),
        "source_product": source_product,
        "target_product": target_product,
        "summary": summary,
        "actions": actions,
    }
    data = json.dumps(doc, indent=2)
    path = out_dir / "mirror_report.json"
    path.write_text(data)
    (out_dir / "mirror_report.json.sha256").write_text(_sha256_str(data))
    return path


def write_changelog(
    out_dir: Path,
    entries: list[dict[str, str]],
) -> Path:
    """Write changelog.md — human-readable mirror log."""
    lines = [
        f"# Soma Mirror Changelog",
        f"",
        f"Generated: {_now_iso()}",
        f"",
    ]
    if not entries:
        lines.append("No changes detected.")
    else:
        lines.append(f"## {len(entries)} change(s)")
        lines.append("")
        for e in entries:
            action = e.get("action", "unknown")
            title = e.get("title", "untitled")
            detail = e.get("detail", "")
            lines.append(f"- **{action}**: {title}")
            if detail:
                lines.append(f"  - {detail}")
        lines.append("")
    path = out_dir / "changelog.md"
    path.write_text("\n".join(lines))
    return path


def write_run_manifest(
    out_dir: Path,
    run_id: str,
    workflow: str,
    status: str,
    artifacts_written: list[str],
    error: str | None = None,
) -> Path:
    """Write _manifest.json — top-level run metadata."""
    doc = {
        "run_id": run_id,
        "workflow": workflow,
        "status": status,
        "timestamp": _now_iso(),
        "artifacts": artifacts_written,
    }
    if error:
        doc["error"] = error
    path = out_dir / "_manifest.json"
    path.write_text(json.dumps(doc, indent=2))
    return path
