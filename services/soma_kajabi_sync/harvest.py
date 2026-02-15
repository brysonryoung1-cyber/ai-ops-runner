#!/usr/bin/env python3
"""harvest_gmail_zane — Harvest video list + metadata from Zane's Gmail.

Usage:
    python -m soma_kajabi_sync.harvest
    python -m soma_kajabi_sync.harvest --smoke  # smoke test

Produces:
    artifacts/soma/<run_id>/gmail_video_index.json
    artifacts/soma/<run_id>/video_manifest.csv

Reads Gmail via IMAP with app password. Never stores plaintext passwords in repo.
"""

from __future__ import annotations

import argparse
import email
import email.header
import imaplib
import json
import re
import sys
import uuid
from datetime import datetime, timezone
from typing import Any

from .artifacts import (
    write_gmail_video_index,
    write_run_manifest,
    write_video_manifest_csv,
)
from .config import get_artifacts_dir, load_secret, mask_secret


def _generate_run_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    short = uuid.uuid4().hex[:8]
    return f"harvest_{ts}_{short}"


def _decode_header(raw: str | None) -> str:
    """Decode a possibly-encoded email header."""
    if not raw:
        return ""
    parts = email.header.decode_header(raw)
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return " ".join(decoded)


# Video file extensions and URL patterns we look for
_VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}
_VIDEO_URL_RE = re.compile(
    r"https?://[^\s<>\"']+\.(?:mp4|mov|avi|mkv|webm|m4v)",
    re.IGNORECASE,
)
_DRIVE_LINK_RE = re.compile(
    r"https?://drive\.google\.com/[^\s<>\"']+",
    re.IGNORECASE,
)
_VIMEO_LINK_RE = re.compile(
    r"https?://(?:www\.)?vimeo\.com/[^\s<>\"']+",
    re.IGNORECASE,
)
_YOUTUBE_LINK_RE = re.compile(
    r"https?://(?:www\.)?(?:youtube\.com|youtu\.be)/[^\s<>\"']+",
    re.IGNORECASE,
)


def _extract_video_refs(msg: email.message.Message) -> list[dict[str, str]]:
    """Extract video references from an email message."""
    refs: list[dict[str, str]] = []
    seen_urls: set[str] = set()

    # Check attachments
    for part in msg.walk():
        filename = part.get_filename()
        if filename:
            fname_lower = filename.lower()
            for ext in _VIDEO_EXTENSIONS:
                if fname_lower.endswith(ext):
                    refs.append(
                        {
                            "type": "attachment",
                            "filename": filename,
                            "content_type": part.get_content_type() or "unknown",
                        }
                    )
                    break

    # Check body for links
    for part in msg.walk():
        ct = part.get_content_type()
        if ct not in ("text/plain", "text/html"):
            continue
        try:
            body = part.get_payload(decode=True)
            if not body:
                continue
            text = body.decode("utf-8", errors="replace")
        except Exception:
            continue

        for pattern, link_type in [
            (_VIDEO_URL_RE, "direct_video_url"),
            (_DRIVE_LINK_RE, "google_drive"),
            (_VIMEO_LINK_RE, "vimeo"),
            (_YOUTUBE_LINK_RE, "youtube"),
        ]:
            for match in pattern.finditer(text):
                url = match.group()
                if url not in seen_urls:
                    seen_urls.add(url)
                    refs.append({"type": link_type, "url": url})

    return refs


def harvest_gmail(smoke: bool = False) -> dict[str, Any]:
    """Main entrypoint: harvest video metadata from Zane's Gmail.

    Returns a result dict with status and artifact paths.
    """
    run_id = _generate_run_id()
    out_dir = get_artifacts_dir(run_id)

    print("=== harvest_gmail_zane ===")
    print(f"  Run ID:  {run_id}")
    print(f"  Out dir: {out_dir}")
    print()

    if smoke:
        # Smoke test: synthetic data
        print("  [SMOKE MODE] Using synthetic data (no credentials required)")
        email_records = [
            {
                "email_id": "smoke-msg-001",
                "subject": "New videos for Home Library - Module 3",
                "from": "zane@example.com",
                "date": "2026-01-15T10:30:00Z",
                "video_refs": [
                    {"type": "google_drive", "url": "https://drive.google.com/file/d/smoke-001/view"},
                    {"type": "attachment", "filename": "module3_intro.mp4", "content_type": "video/mp4"},
                ],
            },
            {
                "email_id": "smoke-msg-002",
                "subject": "Updated practitioner content",
                "from": "zane@example.com",
                "date": "2026-02-01T14:00:00Z",
                "video_refs": [
                    {"type": "vimeo", "url": "https://vimeo.com/smoke-002"},
                ],
            },
        ]
        manifest_rows = [
            {
                "video_id": "v-smoke-001",
                "title": "Module 3 Intro",
                "source_email_id": "smoke-msg-001",
                "date_received": "2026-01-15",
                "status": "mapped",
                "kajabi_product": "Home User Library",
                "kajabi_category": "Module 3",
                "file_url": "https://drive.google.com/file/d/smoke-001/view",
                "notes": "",
            },
            {
                "video_id": "v-smoke-002",
                "title": "module3_intro.mp4",
                "source_email_id": "smoke-msg-001",
                "date_received": "2026-01-15",
                "status": "unmapped",
                "kajabi_product": "",
                "kajabi_category": "",
                "file_url": "",
                "notes": "Attachment - needs manual mapping",
            },
            {
                "video_id": "v-smoke-003",
                "title": "Updated practitioner content",
                "source_email_id": "smoke-msg-002",
                "date_received": "2026-02-01",
                "status": "raw_needs_review",
                "kajabi_product": "",
                "kajabi_category": "",
                "file_url": "https://vimeo.com/smoke-002",
                "notes": "Vimeo link - needs review",
            },
        ]
    else:
        # Load Gmail credentials (fail-closed)
        gmail_user = load_secret("GMAIL_USER")
        gmail_pass = load_secret("GMAIL_APP_PASSWORD")
        assert gmail_user is not None
        assert gmail_pass is not None
        print(f"  Gmail:   {mask_secret(gmail_user)}")

        # Connect to Gmail via IMAP
        print("  Connecting to Gmail IMAP...")
        try:
            imap = imaplib.IMAP4_SSL("imap.gmail.com", 993)
            imap.login(gmail_user, gmail_pass)
        except imaplib.IMAP4.error as e:
            print(f"ERROR: Gmail IMAP login failed: {e}", file=sys.stderr)
            sys.exit(1)

        # Search for emails from Zane with video content
        imap.select("INBOX")
        # Search criteria: from Zane, with attachments or video keywords
        search_queries = [
            '(FROM "zane" SUBJECT "video")',
            '(FROM "zane" SUBJECT "library")',
            '(FROM "zane" SUBJECT "module")',
            '(FROM "zane" SUBJECT "content")',
        ]

        all_msg_ids: set[bytes] = set()
        for query in search_queries:
            try:
                _, msg_ids_raw = imap.search(None, query)
                for mid in msg_ids_raw[0].split():
                    if mid:
                        all_msg_ids.add(mid)
            except imaplib.IMAP4.error:
                continue

        print(f"  Found {len(all_msg_ids)} matching emails")

        email_records = []
        manifest_rows = []
        video_counter = 0

        for msg_id in sorted(all_msg_ids):
            try:
                _, msg_data = imap.fetch(msg_id, "(RFC822)")
                if not msg_data or not msg_data[0]:
                    continue
                raw_email = msg_data[0]
                if isinstance(raw_email, tuple):
                    raw_email = raw_email[1]
                msg = email.message_from_bytes(
                    raw_email if isinstance(raw_email, bytes) else raw_email.encode()
                )
            except Exception as e:
                print(f"  WARN: Failed to parse message {msg_id}: {e}", file=sys.stderr)
                continue

            subject = _decode_header(msg.get("Subject"))
            from_addr = _decode_header(msg.get("From"))
            date_str = _decode_header(msg.get("Date"))
            email_id = msg.get("Message-ID", str(msg_id))

            video_refs = _extract_video_refs(msg)
            if not video_refs:
                continue

            record = {
                "email_id": email_id,
                "subject": subject,
                "from": from_addr,
                "date": date_str,
                "video_refs": video_refs,
            }
            email_records.append(record)

            # Create manifest rows for each video reference
            for ref in video_refs:
                video_counter += 1
                vid_id = f"v-{run_id}-{video_counter:04d}"
                title = ref.get("filename", subject)
                file_url = ref.get("url", "")
                ref_type = ref.get("type", "unknown")

                # Determine initial status
                if ref_type == "attachment":
                    status = "unmapped"
                    notes = f"Attachment ({ref.get('content_type', 'unknown')})"
                elif ref_type in ("google_drive", "vimeo", "youtube"):
                    status = "raw_needs_review"
                    notes = f"{ref_type} link - needs review"
                else:
                    status = "raw_needs_review"
                    notes = "Direct video URL"

                manifest_rows.append(
                    {
                        "video_id": vid_id,
                        "title": title,
                        "source_email_id": email_id,
                        "date_received": date_str,
                        "status": status,
                        "kajabi_product": "",
                        "kajabi_category": "",
                        "file_url": file_url,
                        "notes": notes,
                    }
                )

        imap.logout()

    # Write artifacts
    idx_path = write_gmail_video_index(out_dir, email_records)
    csv_path = write_video_manifest_csv(out_dir, manifest_rows)

    # Count statuses
    status_counts = {"mapped": 0, "unmapped": 0, "raw_needs_review": 0}
    for row in manifest_rows:
        s = row.get("status", "unknown")
        if s in status_counts:
            status_counts[s] += 1

    print(f"\n  Emails processed: {len(email_records)}")
    print(f"  Videos found:     {len(manifest_rows)}")
    print(f"  Status counts:    {json.dumps(status_counts)}")
    print(f"  Index:            {idx_path}")
    print(f"  Manifest:         {csv_path}")

    artifacts_written = [
        "gmail_video_index.json",
        "gmail_video_index.json.sha256",
        "video_manifest.csv",
        "video_manifest.csv.sha256",
    ]
    write_run_manifest(out_dir, run_id, "harvest_gmail_zane", "success", artifacts_written)

    result = {
        "status": "success",
        "run_id": run_id,
        "artifacts_dir": str(out_dir),
        "artifacts": artifacts_written,
        "total_emails": len(email_records),
        "total_videos": len(manifest_rows),
        "status_counts": status_counts,
    }
    print(f"\n  Result: {json.dumps(result, indent=2)}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Harvest video metadata from Zane's Gmail"
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="Smoke test mode — synthetic data, no credentials needed",
    )
    args = parser.parse_args()
    harvest_gmail(smoke=args.smoke)


if __name__ == "__main__":
    main()
