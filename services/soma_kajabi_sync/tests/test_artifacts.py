"""Hermetic tests for Soma artifacts module.

All tests are fully isolated — no network, no credentials, no side effects.
"""

import csv
import hashlib
import json
import tempfile
from pathlib import Path

import pytest

# Adjust import path for test discovery
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from soma_kajabi_sync.artifacts import (
    write_changelog,
    write_gmail_video_index,
    write_mirror_report,
    write_run_manifest,
    write_snapshot_json,
    write_video_manifest_csv,
)


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


class TestSnapshotJson:
    def test_writes_valid_json(self, tmp_dir: Path):
        categories = [
            {
                "name": "Module 1",
                "slug": "module-1",
                "items": [
                    {"title": "Intro", "slug": "intro", "type": "video", "published": True, "position": 0},
                ],
            },
        ]
        path = write_snapshot_json(tmp_dir, "Home User Library", categories)
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["product"] == "Home User Library"
        assert data["total_categories"] == 1
        assert data["total_items"] == 1
        assert data["schema_version"] == 1
        assert "captured_at" in data

    def test_sha256_sidecar(self, tmp_dir: Path):
        path = write_snapshot_json(tmp_dir, "Test", [])
        sha_path = Path(str(path) + ".sha256")
        assert sha_path.exists()
        expected = hashlib.sha256(path.read_text().encode()).hexdigest()
        assert sha_path.read_text() == expected

    def test_empty_categories(self, tmp_dir: Path):
        path = write_snapshot_json(tmp_dir, "Empty", [])
        data = json.loads(path.read_text())
        assert data["total_categories"] == 0
        assert data["total_items"] == 0


class TestVideoManifestCsv:
    def test_writes_valid_csv(self, tmp_dir: Path):
        rows = [
            {
                "video_id": "v-001",
                "title": "Test Video",
                "source_email_id": "msg-001",
                "date_received": "2026-01-01",
                "status": "mapped",
                "kajabi_product": "Home User Library",
                "kajabi_category": "Module 1",
                "file_url": "https://example.com/video.mp4",
                "notes": "",
            },
        ]
        path = write_video_manifest_csv(tmp_dir, rows)
        assert path.exists()
        with open(path) as f:
            reader = csv.DictReader(f)
            result = list(reader)
        assert len(result) == 1
        assert result[0]["video_id"] == "v-001"
        assert result[0]["status"] == "mapped"

    def test_all_statuses(self, tmp_dir: Path):
        rows = [
            {"video_id": f"v-{i}", "title": f"V{i}", "status": s}
            for i, s in enumerate(["mapped", "unmapped", "raw_needs_review"])
        ]
        path = write_video_manifest_csv(tmp_dir, rows)
        with open(path) as f:
            reader = csv.DictReader(f)
            statuses = [r["status"] for r in reader]
        assert set(statuses) == {"mapped", "unmapped", "raw_needs_review"}


class TestGmailVideoIndex:
    def test_writes_valid_json(self, tmp_dir: Path):
        emails = [
            {"email_id": "msg-001", "subject": "Test", "from": "test@example.com"},
        ]
        path = write_gmail_video_index(tmp_dir, emails)
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["total_emails"] == 1
        assert data["schema_version"] == 1


class TestMirrorReport:
    def test_writes_valid_json(self, tmp_dir: Path):
        actions = [
            {"action": "add_item", "title": "New Video", "category": "Module 1"},
        ]
        summary = {"total_actions": 1, "add_item": 1}
        path = write_mirror_report(
            tmp_dir, "Home User Library", "Practitioner Library", actions, summary
        )
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["source_product"] == "Home User Library"
        assert data["summary"]["total_actions"] == 1


class TestChangelog:
    def test_writes_markdown(self, tmp_dir: Path):
        entries = [
            {"action": "add_item", "title": "New Video", "detail": "Added to Module 1"},
        ]
        path = write_changelog(tmp_dir, entries)
        assert path.exists()
        content = path.read_text()
        assert "# Soma Mirror Changelog" in content
        assert "**add_item**" in content
        assert "New Video" in content

    def test_empty_entries(self, tmp_dir: Path):
        path = write_changelog(tmp_dir, [])
        content = path.read_text()
        assert "No changes detected" in content


class TestRunManifest:
    def test_writes_manifest(self, tmp_dir: Path):
        path = write_run_manifest(
            tmp_dir, "run-001", "snapshot_kajabi", "success",
            ["snapshot.json", "snapshot.json.sha256"],
        )
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["run_id"] == "run-001"
        assert data["workflow"] == "snapshot_kajabi"
        assert data["status"] == "success"
        assert "error" not in data

    def test_writes_manifest_with_error(self, tmp_dir: Path):
        path = write_run_manifest(
            tmp_dir, "run-002", "harvest", "error", [],
            error="IMAP login failed",
        )
        data = json.loads(path.read_text())
        assert data["status"] == "error"
        assert data["error"] == "IMAP login failed"
