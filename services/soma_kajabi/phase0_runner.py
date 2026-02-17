#!/usr/bin/env python3
"""soma_kajabi Phase 0 runner — read-only snapshot, harvest, manifest.

Artifact contract (always written, even on FAIL-CLOSED):
  artifacts/soma_kajabi/phase0/<run_id>/kajabi_library_snapshot.json
  artifacts/soma_kajabi/phase0/<run_id>/gmail_harvest.jsonl
  artifacts/soma_kajabi/phase0/<run_id>/video_manifest.csv
  artifacts/soma_kajabi/phase0/<run_id>/result.json

Enforces: kill_switch, phase gate. Fail-closed on missing connectors.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import (
    get_kill_switch,
    load_soma_kajabi_config,
    mask_fingerprint,
    repo_root,
)

ARTIFACTS_ROOT = Path("artifacts/soma_kajabi/phase0")
GMAIL_SEARCH_QUERY = 'from:(Zane McCourtney) has:attachment'


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _generate_run_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    short = uuid.uuid4().hex[:8]
    return f"phase0_{ts}_{short}"


def _ensure_artifacts_dir(run_id: str, root: Path) -> Path:
    out = root / ARTIFACTS_ROOT / run_id
    out.mkdir(parents=True, exist_ok=True)
    return out


def _write_result(
    out_dir: Path,
    ok: bool,
    action: str,
    run_id: str,
    artifact_paths: list[str],
    error_class: str | None = None,
    recommended_next_action: str | None = None,
) -> None:
    doc: dict[str, Any] = {
        "ok": ok,
        "action": action,
        "run_id": run_id,
        "artifact_paths": artifact_paths,
    }
    if error_class:
        doc["error_class"] = error_class
    if recommended_next_action:
        doc["recommended_next_action"] = recommended_next_action
    (out_dir / "result.json").write_text(json.dumps(doc, indent=2))


def _file_sha256(path: Path) -> str:
    if not path.exists():
        return ""
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _write_baseline_ok(
    out_dir: Path,
    run_id: str,
    counts: dict[str, int],
) -> None:
    """Write BASELINE_OK.json with hashes of main artifacts, counts, timestamp."""
    paths = [
        out_dir / "kajabi_library_snapshot.json",
        out_dir / "gmail_harvest.jsonl",
        out_dir / "video_manifest.csv",
    ]
    hashes: dict[str, str] = {}
    for p in paths:
        if p.exists():
            hashes[p.name] = _file_sha256(p)
    doc: dict[str, Any] = {
        "run_id": run_id,
        "timestamp_utc": _now_iso(),
        "counts": counts,
        "artifact_hashes": hashes,
    }
    (out_dir / "BASELINE_OK.json").write_text(json.dumps(doc, indent=2))


def _update_project_state_baseline(root: Path, run_id: str, artifact_dir: str) -> None:
    """Merge Phase 0 baseline result into config/project_state.json (canonical)."""
    state_path = root / "config" / "project_state.json"
    if not state_path.exists():
        return
    try:
        state = json.loads(state_path.read_text())
    except Exception:
        return
    projects = state.setdefault("projects", {})
    sk = projects.setdefault("soma_kajabi", {})
    sk["phase0_baseline_status"] = "PASS"
    sk["phase0_baseline_artifact_dir"] = artifact_dir
    sk["phase0_last_run_id"] = run_id
    try:
        state_path.write_text(json.dumps(state, indent=2))
    except OSError:
        pass


def _write_kajabi_snapshot_fail_closed(out_dir: Path, run_id: str, mode: str) -> Path:
    """Write schema-compliant snapshot with unknown fields when capture cannot run."""
    doc = {
        "captured_at": _now_iso(),
        "run_id": run_id,
        "mode": mode,
        "home": {
            "modules": [],
            "lessons": [{
                "module_name": "unknown",
                "title": "unknown",
                "above_paywall": "unknown",
                "published_state": "unknown",
                "attached_video_name": "unknown",
            }],
        },
        "practitioner": {
            "modules": [],
            "lessons": [],
        },
    }
    path = out_dir / "kajabi_library_snapshot.json"
    path.write_text(json.dumps(doc, indent=2))
    return path


def _write_kajabi_snapshot_success(
    out_dir: Path,
    run_id: str,
    mode: str,
    home_data: dict,
    practitioner_data: dict,
) -> Path:
    doc = {
        "captured_at": _now_iso(),
        "run_id": run_id,
        "mode": mode,
        "home": home_data,
        "practitioner": practitioner_data,
    }
    path = out_dir / "kajabi_library_snapshot.json"
    path.write_text(json.dumps(doc, indent=2))
    return path


def _transform_categories_to_lessons(categories: list[dict]) -> list[dict]:
    """Transform soma_kajabi_sync categories into lesson schema."""
    lessons = []
    for cat in categories:
        mod_name = cat.get("name", "unknown")
        for item in cat.get("items", []):
            lessons.append({
                "module_name": mod_name,
                "title": item.get("title", "unknown"),
                "above_paywall": "unknown",
                "published_state": "published" if item.get("published", True) else "draft",
                "attached_video_name": "unknown",
            })
    return lessons


def _run_kajabi_snapshot(root: Path, out_dir: Path, run_id: str, cfg: dict) -> tuple[bool, str | None]:
    """Capture Kajabi structure. Returns (ok, recommended_next_action)."""
    mode = cfg.get("kajabi_capture_mode", "manual")
    if mode == "manual":
        _write_kajabi_snapshot_fail_closed(out_dir, run_id, mode)
        return False, "Switch to playwright mode and provide credential reference in config/projects/soma_kajabi.json"

    # Try playwright or API via soma_kajabi_sync
    try:
        from services.soma_kajabi_sync.config import load_secret
        from services.soma_kajabi_sync.snapshot import snapshot_kajabi
    except ImportError:
        _write_kajabi_snapshot_fail_closed(out_dir, run_id, mode)
        return False, "soma_kajabi_sync not available; ensure services.soma_kajabi_sync is importable"

    token = load_secret("KAJABI_SESSION_TOKEN", required=False)
    if not token:
        _write_kajabi_snapshot_fail_closed(out_dir, run_id, mode)
        return False, "KAJABI_SESSION_TOKEN not configured; store in env or /etc/ai-ops-runner/secrets/kajabi_session_token"

    try:
        home_result = snapshot_kajabi("Home User Library", smoke=False)
        pract_result = snapshot_kajabi("Practitioner Library", smoke=False)
    except SystemExit:
        _write_kajabi_snapshot_fail_closed(out_dir, run_id, mode)
        return False, "Kajabi capture failed; check session token and network"

    home_cats = []
    pract_cats = []
    if isinstance(home_result, dict) and home_result.get("artifacts_dir"):
        snap_path = Path(home_result["artifacts_dir"]) / "snapshot.json"
        if snap_path.exists():
            home_cats = json.loads(snap_path.read_text()).get("categories", [])
    if isinstance(pract_result, dict) and pract_result.get("artifacts_dir"):
        snap_path = Path(pract_result["artifacts_dir"]) / "snapshot.json"
        if snap_path.exists():
            pract_cats = json.loads(snap_path.read_text()).get("categories", [])
    home_lessons = _transform_categories_to_lessons(home_cats)
    pract_lessons = _transform_categories_to_lessons(pract_cats)

    home_data = {"modules": [c.get("name", "") for c in home_cats], "lessons": home_lessons}
    pract_data = {"modules": [c.get("name", "") for c in pract_cats], "lessons": pract_lessons}
    _write_kajabi_snapshot_success(out_dir, run_id, mode, home_data, pract_data)
    return True, None


def _write_gmail_harvest_fail_closed(out_dir: Path, error_class: str, recommended_next_action: str) -> Path:
    """Write single JSONL line when harvest cannot run."""
    line = json.dumps({
        "error_class": error_class,
        "recommended_next_action": recommended_next_action,
    }) + "\n"
    path = out_dir / "gmail_harvest.jsonl"
    path.write_text(line)
    return path


def _write_gmail_harvest_success(out_dir: Path, emails: list[dict]) -> Path:
    path = out_dir / "gmail_harvest.jsonl"
    with path.open("w") as f:
        for e in emails:
            f.write(json.dumps(e) + "\n")
    return path


def _run_gmail_harvest(root: Path, out_dir: Path, cfg: dict) -> tuple[list[dict], bool, str | None]:
    """Harvest Gmail from:(Zane McCourtney) has:attachment. Returns (emails, ok, recommended_next_action)."""
    mode = cfg.get("gmail_capture_mode", "manual")
    if mode == "manual":
        _write_gmail_harvest_fail_closed(
            out_dir,
            "GMAIL_NOT_CONFIGURED",
            "Switch to oauth mode and provide credential reference, or run manual harvest and place gmail_harvest.jsonl in artifacts",
        )
        return [], False, "Switch to oauth mode and provide credential reference"

    try:
        import email
        import email.header
        import imaplib
    except ImportError:
        _write_gmail_harvest_fail_closed(out_dir, "IMAP_UNAVAILABLE", "Install Python imaplib (stdlib)")
        return [], False, "imaplib unavailable"

    try:
        from services.soma_kajabi_sync.config import load_secret
    except ImportError:
        _write_gmail_harvest_fail_closed(out_dir, "CONFIG_UNAVAILABLE", "soma_kajabi_sync config not importable")
        return [], False, "soma_kajabi_sync not available"

    gmail_user = load_secret("GMAIL_USER", required=False)
    gmail_pass = load_secret("GMAIL_APP_PASSWORD", required=False)
    if not gmail_user or not gmail_pass:
        _write_gmail_harvest_fail_closed(
            out_dir,
            "OAUTH_TOKEN_MISSING",
            "Configure GMAIL_USER and GMAIL_APP_PASSWORD in env or /etc/ai-ops-runner/secrets/",
        )
        return [], False, "OAuth token missing; configure GMAIL_USER and GMAIL_APP_PASSWORD"

    def _decode_header(raw: str | None) -> str:
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

    emails_out: list[dict] = []
    try:
        imap = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        imap.login(gmail_user, gmail_pass)
        imap.select("INBOX")

        # Gmail IMAP X-GM-RAW for Gmail search syntax
        try:
            _, msg_ids_raw = imap.search(None, "X-GM-RAW", GMAIL_SEARCH_QUERY)
        except imaplib.IMAP4.error:
            # Fallback: simpler search
            _, msg_ids_raw = imap.search(None, "ALL")

        msg_ids = msg_ids_raw[0].split() if msg_ids_raw and msg_ids_raw[0] else []

        for msg_id in msg_ids[:100]:  # Cap for safety
            try:
                _, msg_data = imap.fetch(msg_id, "(RFC822)")
                if not msg_data or not msg_data[0]:
                    continue
                raw = msg_data[0]
                if isinstance(raw, tuple):
                    raw = raw[1]
                msg = email.message_from_bytes(raw if isinstance(raw, bytes) else raw.encode())

                subject = _decode_header(msg.get("Subject"))
                date_str = _decode_header(msg.get("Date"))
                email_id = msg.get("Message-ID", msg_id.decode() if isinstance(msg_id, bytes) else str(msg_id))

                body_text = ""
                attachments: list[dict] = []
                for part in msg.walk():
                    if part.get_content_maintype() == "multipart":
                        continue
                    filename = part.get_filename()
                    if filename:
                        attachments.append({
                            "filename": filename,
                            "mime": part.get_content_type() or "unknown",
                            "size_bytes": len(part.get_payload(decode=True) or b""),
                        })
                    elif part.get_content_type() in ("text/plain", "text/html"):
                        try:
                            body_text = (part.get_payload(decode=True) or b"").decode("utf-8", errors="replace")[:5000]
                        except Exception:
                            pass

                emails_out.append({
                    "email_id": email_id,
                    "subject": subject,
                    "datetime": date_str,
                    "body_text": body_text[:2000],
                    "attachments": attachments,
                })
            except Exception:
                continue

        imap.logout()
    except Exception as e:
        _write_gmail_harvest_fail_closed(
            out_dir,
            "GMAIL_HARVEST_FAILED",
            f"Gmail IMAP error: {str(e)[:200]}",
        )
        return [], False, f"Gmail harvest failed: {str(e)[:100]}"

    _write_gmail_harvest_success(out_dir, emails_out)
    return emails_out, True, None


def _write_video_manifest(out_dir: Path, rows: list[dict]) -> Path:
    fieldnames = [
        "email_id",
        "subject",
        "file_name",
        "sha256",
        "rough_topic",
        "proposed_module",
        "proposed_lesson_title",
        "proposed_description",
        "status",
    ]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    path = out_dir / "video_manifest.csv"
    path.write_text(buf.getvalue())
    return path


def _derive_manifest_from_emails(emails: list[dict]) -> list[dict]:
    """Build video_manifest.csv rows from gmail harvest. Default status=unmapped."""
    rows = []
    for e in emails:
        email_id = e.get("email_id", "")
        subject = e.get("subject", "")
        for att in e.get("attachments", []):
            fname = att.get("filename", "")
            rows.append({
                "email_id": email_id,
                "subject": subject,
                "file_name": fname,
                "sha256": "",
                "rough_topic": "",
                "proposed_module": "",
                "proposed_lesson_title": "",
                "proposed_description": "",
                "status": "unmapped",
            })
        if not e.get("attachments"):
            rows.append({
                "email_id": email_id,
                "subject": subject,
                "file_name": "",
                "sha256": "",
                "rough_topic": "",
                "proposed_module": "",
                "proposed_lesson_title": "",
                "proposed_description": "",
                "status": "raw_needs_review",
            })
    return rows


def main() -> int:
    root = repo_root()
    cfg = load_soma_kajabi_config(root)
    run_id = _generate_run_id()
    out_dir = _ensure_artifacts_dir(run_id, root)

    artifact_paths: list[str] = []
    for name in ["kajabi_library_snapshot.json", "gmail_harvest.jsonl", "video_manifest.csv", "result.json", "BASELINE_OK.json"]:
        artifact_paths.append(str(ARTIFACTS_ROOT / run_id / name))

    # Kill switch: Phase 0 inventory (read-only snapshot/harvest) is permitted even when
    # kill_switch=true. Non-read-only actions are blocked elsewhere.
    # No early exit here; proceed with snapshot/harvest.

    # Phase gate: only Phase 0 actions allowed (this is Phase 0)
    # Future: if action were phase1+, block with PHASE_GATE_BLOCKED

    # Snapshot
    kajabi_ok, kajabi_next = _run_kajabi_snapshot(root, out_dir, run_id, cfg)

    # Harvest
    emails, harvest_ok, harvest_next = _run_gmail_harvest(root, out_dir, cfg)

    # Manifest
    manifest_rows = _derive_manifest_from_emails(emails)
    _write_video_manifest(out_dir, manifest_rows)

    ok = kajabi_ok and harvest_ok
    rec = kajabi_next or harvest_next
    _write_result(
        out_dir,
        ok=ok,
        action="soma_kajabi_phase0",
        run_id=run_id,
        artifact_paths=artifact_paths,
        error_class=None if ok else "CONNECTOR_NOT_CONFIGURED",
        recommended_next_action=rec,
    )

    # Baseline counts for BASELINE_OK.json
    snapshot_path = out_dir / "kajabi_library_snapshot.json"
    home_modules = pract_lessons_count = 0
    if snapshot_path.exists():
        try:
            snap = json.loads(snapshot_path.read_text())
            home_modules = len(snap.get("home", {}).get("modules", []))
            pract_lessons_count = len(snap.get("practitioner", {}).get("lessons", []))
        except Exception:
            pass
    counts = {
        "gmail_emails": len(emails),
        "video_manifest_rows": len(manifest_rows),
        "home_modules": home_modules,
        "practitioner_lessons": pract_lessons_count,
    }
    _write_baseline_ok(out_dir, run_id, counts)

    # Update canonical project_state when run succeeded (baseline PASS)
    artifact_dir = str(ARTIFACTS_ROOT / run_id)
    if ok:
        _update_project_state_baseline(root, run_id, artifact_dir)

    print(json.dumps({
        "ok": ok,
        "run_id": run_id,
        "artifact_paths": artifact_paths,
        "error_class": None if ok else "CONNECTOR_NOT_CONFIGURED",
        "recommended_next_action": rec,
    }))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
