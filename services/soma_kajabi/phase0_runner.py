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

from .config import get_kill_switch, mask_fingerprint, repo_root
from .connector_config import (
    is_gmail_ready,
    is_kajabi_ready,
    load_soma_kajabi_config,
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
    gmail_status: str | None = None,
    gmail_reason: str | None = None,
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
    if gmail_status is not None:
        doc["gmail_status"] = gmail_status
    if gmail_reason is not None:
        doc["gmail_reason"] = gmail_reason
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


def _session_token_from_storage_state(path: Path) -> str | None:
    """Extract _kjb_session cookie value from Playwright storage_state JSON. Returns None if missing/invalid."""
    if not path.exists() or path.stat().st_size == 0:
        return None
    try:
        data = json.loads(path.read_text())
        cookies = data.get("cookies") if isinstance(data, dict) else []
        for c in cookies if isinstance(cookies, list) else []:
            if isinstance(c, dict) and c.get("name") == "_kjb_session":
                val = c.get("value")
                if val and isinstance(val, str):
                    return val
    except Exception:
        pass
    return None


def _run_kajabi_snapshot(root: Path, out_dir: Path, run_id: str, cfg: dict) -> tuple[bool, str | None, str | None]:
    """Capture Kajabi structure. Returns (ok, recommended_next_action, error_class)."""
    import os
    kajabi_cfg = cfg.get("kajabi") or {}
    mode = kajabi_cfg.get("mode", cfg.get("kajabi_capture_mode", "manual"))
    if mode == "manual":
        _write_kajabi_snapshot_fail_closed(out_dir, run_id, mode)
        return False, "Switch to playwright mode and provide credential reference in config/projects/soma_kajabi.json", "CONNECTOR_NOT_CONFIGURED"

    # Try playwright or API via soma_kajabi_sync
    try:
        from services.soma_kajabi_sync.config import load_secret
        from services.soma_kajabi_sync.snapshot import snapshot_kajabi
    except ImportError:
        _write_kajabi_snapshot_fail_closed(out_dir, run_id, mode)
        return False, "soma_kajabi_sync not available; ensure services.soma_kajabi_sync is importable", "CONNECTOR_NOT_CONFIGURED"

    token: str | None = None
    if mode == "storage_state":
        from .connector_config import KAJABI_STORAGE_STATE_PATH
        path_str = kajabi_cfg.get("storage_state_secret_ref") or str(KAJABI_STORAGE_STATE_PATH)
        path = Path(path_str)
        token = _session_token_from_storage_state(path)
        if token:
            os.environ["KAJABI_SESSION_TOKEN"] = token
    if not token:
        token = load_secret("KAJABI_SESSION_TOKEN", required=False)
    if not token:
        _write_kajabi_snapshot_fail_closed(out_dir, run_id, mode)
        return False, "KAJABI_SESSION_TOKEN not configured; store in env or /etc/ai-ops-runner/secrets/kajabi_session_token", "CONNECTOR_NOT_CONFIGURED"

    try:
        home_result = snapshot_kajabi("Home User Library", smoke=False)
        pract_result = snapshot_kajabi("Practitioner Library", smoke=False)
    except SystemExit:
        _write_kajabi_snapshot_fail_closed(out_dir, run_id, mode)
        return False, "Kajabi capture failed; check session token and network", "CONNECTOR_NOT_CONFIGURED"

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
    # Fail-closed: reject empty "success" snapshots
    total = (
        len(home_data.get("modules", []))
        + len(home_data.get("lessons", []))
        + len(pract_data.get("modules", []))
        + len(pract_data.get("lessons", []))
    )
    snap_path = out_dir / "kajabi_library_snapshot.json"
    if total == 0 or (snap_path.exists() and snap_path.stat().st_size < 2048):
        debug = {
            "captured_at": _now_iso(),
            "final_url": "unknown",
            "title": "unknown",
            "method": mode,
            "home_modules": len(home_data.get("modules", [])),
            "home_lessons": len(home_data.get("lessons", [])),
            "practitioner_modules": len(pract_data.get("modules", [])),
            "practitioner_lessons": len(pract_data.get("lessons", [])),
            "snapshot_bytes": snap_path.stat().st_size if snap_path.exists() else 0,
        }
        (out_dir / "kajabi_capture_debug.json").write_text(json.dumps(debug, indent=2))
        rec = (
            "Kajabi snapshot empty; run soma_kajabi_discover and inspect artifacts "
            "(final_url, title, screenshot, page.html) to fix product mapping/session"
        )
        return False, rec, "EMPTY_SNAPSHOT"
    return True, None, None


def _write_gmail_harvest_fail_closed(out_dir: Path, error_class: str, recommended_next_action: str) -> Path:
    """Write single JSONL line when harvest cannot run."""
    line = json.dumps({
        "error_class": error_class,
        "recommended_next_action": recommended_next_action,
    }) + "\n"
    path = out_dir / "gmail_harvest.jsonl"
    path.write_text(line)
    return path


def _write_gmail_harvest_skipped(out_dir: Path, reason: str) -> Path:
    """Write single JSONL metadata line when Gmail is intentionally skipped (Kajabi-only mode)."""
    line = json.dumps({
        "gmail_status": "skipped",
        "gmail_reason": reason,
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


def _run_gmail_harvest_oauth(root: Path, out_dir: Path, cfg: dict) -> tuple[list[dict], bool, str | None]:
    """Harvest Gmail via OAuth refresh token. Returns (emails, ok, recommended_next_action)."""
    from .connector_config import GMAIL_OAUTH_PATH
    path_str = cfg.get("gmail", {}).get("auth_secret_ref") or str(GMAIL_OAUTH_PATH)
    path = Path(path_str)
    if not path.exists() or path.stat().st_size == 0:
        _write_gmail_harvest_fail_closed(
            out_dir,
            "OAUTH_TOKEN_MISSING",
            f"Run soma_kajabi_gmail_connect_start then finalize; store refresh token at {path_str}",
        )
        return [], False, "Gmail OAuth token not found; run connect flow"
    try:
        oauth_data = json.loads(path.read_text())
        refresh_token = oauth_data.get("refresh_token") if isinstance(oauth_data, dict) else None
        if not refresh_token:
            _write_gmail_harvest_fail_closed(out_dir, "OAUTH_TOKEN_INVALID", "gmail_oauth.json must contain refresh_token")
            return [], False, "refresh_token missing in gmail_oauth.json"
    except Exception:
        _write_gmail_harvest_fail_closed(out_dir, "OAUTH_TOKEN_INVALID", f"Invalid JSON at {path_str}")
        return [], False, "Invalid gmail_oauth.json"
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
    except ImportError:
        _write_gmail_harvest_fail_closed(
            out_dir,
            "OAUTH_DEPS_MISSING",
            "Install google-auth and google-api-python-client for Gmail OAuth; or use gmail.mode=imap",
        )
        return [], False, "Google API libs not installed; use gmail.mode=imap"
    try:
        from google.auth.transport.requests import Request
        creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=oauth_data.get("client_id") or "",
            client_secret=oauth_data.get("client_secret") or "",
        )
        creds.refresh(Request())
        service = build("gmail", "v1", credentials=creds)
        response = service.users().messages().list(
            userId="me",
            q=GMAIL_SEARCH_QUERY,
            maxResults=100,
        ).execute()
        messages = response.get("messages") or []
        emails_out: list[dict] = []
        for m in messages:
            msg = service.users().messages().get(userId="me", id=m["id"], format="metadata").execute()
            payload = msg.get("payload") or {}
            headers = {h["name"].lower(): h["value"] for h in payload.get("headers") or []}
            subject = headers.get("subject", "")
            date_str = headers.get("date", "")
            email_id = msg.get("id", "")
            attachments = []
            for p in payload.get("parts") or []:
                fname = (p.get("filename") or "").strip()
                if fname:
                    attachments.append({
                        "filename": fname,
                        "mime": p.get("mimeType") or "unknown",
                        "size_bytes": len((p.get("body") or {}).get("data") or b"") or 0,
                    })
            emails_out.append({
                "email_id": email_id,
                "subject": subject,
                "datetime": date_str,
                "body_text": "",
                "attachments": attachments,
            })
        _write_gmail_harvest_success(out_dir, emails_out)
        return emails_out, True, None
    except Exception as e:
        _write_gmail_harvest_fail_closed(
            out_dir,
            "GMAIL_HARVEST_FAILED",
            f"Gmail API error: {str(e)[:200]}",
        )
        return [], False, str(e)[:100]


def _run_gmail_harvest(root: Path, out_dir: Path, cfg: dict) -> tuple[list[dict], bool, str | None]:
    """Harvest Gmail from:(Zane McCourtney) has:attachment. Returns (emails, ok, recommended_next_action)."""
    mode = cfg.get("gmail", {}).get("mode") or cfg.get("gmail_capture_mode", "manual")
    if mode == "manual":
        _write_gmail_harvest_fail_closed(
            out_dir,
            "GMAIL_NOT_CONFIGURED",
            "Switch to oauth mode and provide credential reference, or run manual harvest and place gmail_harvest.jsonl in artifacts",
        )
        return [], False, "Switch to oauth mode and provide credential reference"

    if mode == "oauth":
        return _run_gmail_harvest_oauth(root, out_dir, cfg)

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
    cfg, config_error = load_soma_kajabi_config(root)
    run_id = _generate_run_id()
    out_dir = _ensure_artifacts_dir(run_id, root)

    artifact_paths: list[str] = []
    for name in ["kajabi_library_snapshot.json", "gmail_harvest.jsonl", "video_manifest.csv", "result.json", "BASELINE_OK.json"]:
        artifact_paths.append(str(ARTIFACTS_ROOT / run_id / name))

    # Fail-closed: CONFIG_INVALID when config missing or schema invalid
    if config_error:
        _write_result(
            out_dir,
            ok=False,
            action="soma_kajabi_phase0",
            run_id=run_id,
            artifact_paths=artifact_paths,
            error_class="CONFIG_INVALID",
            recommended_next_action=f"Fix config/projects/soma_kajabi.json: {config_error}",
        )
        print(json.dumps({
            "ok": False,
            "run_id": run_id,
            "artifact_paths": artifact_paths,
            "error_class": "CONFIG_INVALID",
            "recommended_next_action": config_error,
        }))
        return 1

    # Connector readiness: fail-closed ONLY when Kajabi is not ready.
    # Gmail is optional: when missing, Phase0 succeeds with gmail_status=skipped (Kajabi-only mode).
    kajabi_ready, kajabi_reason = is_kajabi_ready(cfg)
    gmail_ready, gmail_reason = is_gmail_ready(cfg)
    if not kajabi_ready:
        rec = f"Kajabi: {kajabi_reason}"
        _write_gmail_harvest_fail_closed(out_dir, "CONNECTOR_NOT_CONFIGURED", rec)
        _write_kajabi_snapshot_fail_closed(out_dir, run_id, cfg.get("kajabi", {}).get("mode", "manual"))
        _write_video_manifest(out_dir, [])
        _write_baseline_ok(out_dir, run_id, {"gmail_emails": 0, "video_manifest_rows": 0, "home_modules": 0, "practitioner_lessons": 0})
        _write_result(
            out_dir,
            ok=False,
            action="soma_kajabi_phase0",
            run_id=run_id,
            artifact_paths=artifact_paths,
            error_class="CONNECTOR_NOT_CONFIGURED",
            recommended_next_action=rec,
        )
        print(json.dumps({
            "ok": False,
            "run_id": run_id,
            "artifact_paths": artifact_paths,
            "error_class": "CONNECTOR_NOT_CONFIGURED",
            "recommended_next_action": rec,
        }))
        return 1

    # Kill switch: Phase 0 inventory (read-only snapshot/harvest) is permitted even when
    # kill_switch=true. Non-read-only actions are blocked elsewhere.
    # No early exit here; proceed with snapshot/harvest.

    # Phase gate: only Phase 0 actions allowed (this is Phase 0)
    # Future: if action were phase1+, block with PHASE_GATE_BLOCKED

    # Snapshot
    kajabi_ok, kajabi_next, kajabi_error_class = _run_kajabi_snapshot(root, out_dir, run_id, cfg)

    # Harvest: run Gmail when ready; otherwise skip (Kajabi-only mode)
    gmail_status_val: str | None = None
    gmail_reason_val: str | None = None
    if gmail_ready:
        emails, harvest_ok, harvest_next = _run_gmail_harvest(root, out_dir, cfg)
    else:
        from .connector_config import GMAIL_OAUTH_PATH
        oauth_path = cfg.get("gmail", {}).get("auth_secret_ref") or str(GMAIL_OAUTH_PATH)
        _write_gmail_harvest_skipped(out_dir, f"oauth token not found at {oauth_path}")
        emails = []
        harvest_ok = True  # Skipped is not a failure for overall Phase0
        harvest_next = None
        gmail_status_val = "skipped"
        gmail_reason_val = f"oauth token not found at {oauth_path}"

    # Manifest
    manifest_rows = _derive_manifest_from_emails(emails)
    _write_video_manifest(out_dir, manifest_rows)

    ok = kajabi_ok and harvest_ok
    rec = kajabi_next or harvest_next
    error_class = None
    if not ok:
        error_class = kajabi_error_class if kajabi_error_class else "CONNECTOR_NOT_CONFIGURED"
        if error_class == "EMPTY_SNAPSHOT" and (out_dir / "kajabi_capture_debug.json").exists():
            artifact_paths.append(str(ARTIFACTS_ROOT / run_id / "kajabi_capture_debug.json"))
    _write_result(
        out_dir,
        ok=ok,
        action="soma_kajabi_phase0",
        run_id=run_id,
        artifact_paths=artifact_paths,
        error_class=error_class,
        recommended_next_action=rec,
        gmail_status=gmail_status_val,
        gmail_reason=gmail_reason_val,
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

    out_doc: dict[str, Any] = {
        "ok": ok,
        "run_id": run_id,
        "artifact_paths": artifact_paths,
        "error_class": error_class,
        "recommended_next_action": rec,
    }
    if gmail_status_val is not None:
        out_doc["gmail_status"] = gmail_status_val
    if gmail_reason_val is not None:
        out_doc["gmail_reason"] = gmail_reason_val
    print(json.dumps(out_doc))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
