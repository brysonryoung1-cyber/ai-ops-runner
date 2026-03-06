#!/usr/bin/env python3
"""Kajabi Discover — capture Kajabi admin products and Business DoD pages.

Modes:
  - headless: current Playwright storage_state flow
  - interactive: reuse the Kajabi noVNC profile for an in-session capture

Cloudflare is never a hard failure in headless mode. Instead discover transitions
to HUMAN_ONLY, emits the canonical noVNC URL plus the exact operator instruction,
and exits 0 so callers can surface the gate cleanly.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ID = "soma_kajabi"
KAJABI_PRODUCTS_PATH = Path("/etc/ai-ops-runner/secrets/soma_kajabi/kajabi_products.json")
KAJABI_CHROME_PROFILE_DIR = Path("/var/lib/openclaw/kajabi_chrome_profile")

TARGET_PRODUCTS = ["Home User Library", "Practitioner Library"]
DEFAULT_SITE_ORIGIN = "https://zane-mccourtney.mykajabi.com"
MEMBERSHIPS_PAGE_PATH_CANDIDATES = ["/memberships", "/memberships-soma"]
COMMUNITY_PAGE_PATH = "/community"
PRIVACY_PAGE_PATH = "/privacy-policy"
TERMS_PAGE_PATH = "/terms"
EXPECTED_COMMUNITY_GROUPS = ["Home Users", "Practitioners"]
REQUIRED_OFFER_URLS = ["/offers/q6ntyjef/checkout", "/offers/MHMmHyVZ/checkout"]

MODE_HEADLESS = "headless"
MODE_INTERACTIVE = "interactive"
STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_HUMAN_ONLY = "HUMAN_ONLY"

HUMAN_ONLY_INSTRUCTION = "log in + 2FA, then CLOSE noVNC to release lock"
PROFILE_LOCK_ERROR_CLASS = "KAJABI_INTERACTIVE_PROFILE_LOCKED"
NOTIFICATION_STATE_FILE = "notification_state.json"


def _resolve_storage_state_path() -> Path:
    root = _repo_root()
    sys.path.insert(0, str(root))
    from services.soma_kajabi.connector_config import get_storage_state_path, load_soma_kajabi_config

    cfg, _err = load_soma_kajabi_config(root)
    return get_storage_state_path(cfg)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _repo_root() -> Path:
    env = os.environ.get("OPENCLAW_REPO_ROOT")
    if env and Path(env).exists():
        return Path(env)
    cwd = Path.cwd()
    for _ in range(10):
        if (cwd / "config" / "project_state.json").exists():
            return cwd
        if cwd == cwd.parent:
            break
        cwd = cwd.parent
    return Path(env or "/opt/ai-ops-runner")


def _artifact_dir() -> Path:
    env = os.environ.get("ARTIFACT_DIR")
    if env:
        out = Path(env)
        out.mkdir(parents=True, exist_ok=True)
        return out
    root = _repo_root()
    run_id = f"discover_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"
    out = root / "artifacts" / PROJECT_ID / "discover" / run_id
    out.mkdir(parents=True, exist_ok=True)
    return out


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _write_latest_pointer(
    out_dir: Path,
    *,
    status: str,
    error_class: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    root = _repo_root()
    pointer = root / "artifacts" / PROJECT_ID / "discover" / "LATEST.json"
    rel = str(out_dir)
    try:
        rel = str(out_dir.relative_to(root))
    except ValueError:
        pass
    payload: dict[str, Any] = {
        "run_id": out_dir.name,
        "artifact_dir": str(out_dir),
        "artifact_rel": rel,
        "status": status,
        "updated_at": _now_iso(),
    }
    if error_class:
        payload["error_class"] = error_class
    if extra:
        payload.update(extra)
    try:
        _atomic_write_json(pointer, payload)
    except Exception:
        pass


def _notification_state_path() -> Path:
    return _repo_root() / "artifacts" / PROJECT_ID / "discover" / NOTIFICATION_STATE_FILE


def _read_notification_state() -> dict[str, Any]:
    path = _notification_state_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_notification_state(state: dict[str, Any]) -> None:
    try:
        _atomic_write_json(_notification_state_path(), state)
    except Exception:
        pass


def _send_transition_alert(
    *,
    current_status: str,
    error_class: str,
    proof_path: str,
    novnc_url: str | None = None,
    gate_expiry: str | None = None,
    instruction: str | None = None,
) -> dict[str, Any]:
    state = _read_notification_state()
    previous_status = str(state.get("last_status") or "").strip().upper()
    current_status = current_status.upper()
    should_notify = False
    if previous_status:
        should_notify = previous_status != current_status and (
            current_status == STATUS_HUMAN_ONLY or previous_status == STATUS_HUMAN_ONLY
        )
    else:
        should_notify = current_status == STATUS_HUMAN_ONLY

    result = {
        "status": "SKIPPED",
        "needed": should_notify,
        "sent": False,
        "deduped": False,
        "hash": "",
        "error_class": "",
        "message": "",
        "notify": {},
    }

    if should_notify:
        from ops.lib.notifier import build_alert_hash, send_discord_webhook_alert

        alert_hash = build_alert_hash(
            event_type=f"kajabi_discover:{previous_status or 'NONE'}->{current_status}",
            matrix_status=current_status,
            failed_checks=[error_class],
        )
        result["hash"] = alert_hash
        if str(state.get("last_alert_hash") or "") == alert_hash:
            result["status"] = "DEDUPED"
            result["deduped"] = True
        else:
            if current_status == STATUS_HUMAN_ONLY:
                message = "\n".join(
                    [
                        "Kajabi discover HUMAN_ONLY",
                        f"error_class: {error_class}",
                        f"proof_path: {proof_path}",
                        f"novnc_url: {novnc_url or ''}",
                        f"gate_expiry: {gate_expiry or ''}",
                        f"instruction: {instruction or HUMAN_ONLY_INSTRUCTION}",
                    ]
                )
            else:
                message = "\n".join(
                    [
                        "Kajabi discover SUCCESS",
                        f"error_class: {error_class}",
                        f"proof_path: {proof_path}",
                    ]
                )
            notify = send_discord_webhook_alert(content=message)
            result["notify"] = notify
            result["sent"] = bool(notify.get("ok"))
            result["status"] = "SENT" if result["sent"] else "ERROR"
            result["error_class"] = str(notify.get("error_class") or "")
            result["message"] = str(notify.get("message") or "")
            if result["sent"]:
                state["last_alert_hash"] = alert_hash

    state["last_status"] = current_status
    state["updated_at"] = _now_iso()
    _write_notification_state(state)
    return result


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=(MODE_HEADLESS, MODE_INTERACTIVE),
        default=(
            os.environ.get("KAJABI_DISCOVER_MODE")
            or os.environ.get("OPENCLAW_DISCOVER_MODE")
            or os.environ.get("MODE")
            or MODE_HEADLESS
        ),
    )
    return parser.parse_args(argv)


def _extract_slug_from_url(href: str) -> str:
    if "/products/" in href:
        parts = href.split("/products/")
        if len(parts) >= 2:
            slug = parts[-1].split("/")[0].split("?")[0].strip()
            if slug and slug != "new":
                return slug
    return ""


def _match_product_name(text: str, targets: list[str]) -> str | None:
    text_lower = (text or "").strip().lower()
    for target in targets:
        if target.lower() in text_lower or text_lower in target.lower():
            return target
    return None


def _write_error(out_dir: Path, error_class: str, **kwargs: Any) -> dict[str, Any]:
    doc = {"ok": False, "captured_at": _now_iso(), "error_class": error_class, **kwargs}
    _write_json(out_dir / "debug.json", doc)
    return doc


def _response_status(response: Any) -> int:
    if response is None:
        return 0
    value = getattr(response, "status", None)
    try:
        if callable(value):
            value = value()
        if value is None:
            return 0
        return int(value)
    except Exception:
        return 0


def _response_headers(response: Any) -> dict[str, str]:
    if response is None:
        return {}
    for attr in ("all_headers", "headers"):
        value = getattr(response, attr, None)
        try:
            headers = value() if callable(value) else value
        except Exception:
            continue
        if isinstance(headers, dict):
            return {str(k).lower(): str(v) for k, v in headers.items()}
    return {}


def _build_site_url(site_origin: str, path_or_url: str) -> str:
    if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
        return path_or_url
    if not path_or_url.startswith("/"):
        path_or_url = "/" + path_or_url
    return f"{site_origin.rstrip('/')}{path_or_url}"


def _capture_page(
    page: Any,
    *,
    url: str,
    out_file: Path,
    safe_screenshot: Any | None = None,
    screenshot_path: Path | None = None,
) -> dict[str, Any]:
    status_code = 0
    final_url = url
    error = ""
    ok = False
    content = ""
    headers: dict[str, str] = {}
    try:
        response = page.goto(url, wait_until="load", timeout=30000)
        page.wait_for_load_state("networkidle", timeout=10000)
        status_code = _response_status(response)
        headers = _response_headers(response)
        final_url = getattr(page, "url", url) or url
        content = page.content() or ""
        out_file.write_text(content[:131072], encoding="utf-8")
        if safe_screenshot and screenshot_path:
            safe_screenshot(page, str(screenshot_path))
        ok = 200 <= status_code < 400
    except Exception as exc:
        error = str(exc)[:200]
        final_url = getattr(page, "url", url) or url
        try:
            out_file.write_text("(capture failed)", encoding="utf-8")
        except Exception:
            pass

    return {
        "requested_url": url,
        "final_url": final_url,
        "status": status_code,
        "ok": ok,
        "error": error,
        "headers": headers,
        "content": content,
    }


def _extract_community_json(html: str) -> dict[str, Any]:
    lower = (html or "").lower()
    name = "Soma Community" if "soma community" in lower else ""
    if not name:
        match = re.search(r"<h1[^>]*>(.*?)</h1>", html or "", flags=re.IGNORECASE | re.DOTALL)
        if match:
            name = re.sub(r"<[^>]+>", " ", match.group(1))
            name = " ".join(name.split())[:120]
    groups = [group for group in EXPECTED_COMMUNITY_GROUPS if group.lower() in lower]
    return {
        "name": name,
        "groups": [{"name": group} for group in groups],
        "heuristic": True,
        "captured_at": _now_iso(),
    }


def _capture_required_pages(
    page: Any,
    *,
    out_dir: Path,
    site_origin: str,
    safe_screenshot: Any | None = None,
) -> dict[str, Any]:
    statuses: dict[str, dict[str, Any]] = {}

    memberships_best: dict[str, Any] | None = None
    for path in MEMBERSHIPS_PAGE_PATH_CANDIDATES:
        result = _capture_page(
            page,
            url=_build_site_url(site_origin, path),
            out_file=out_dir / "memberships_page.html",
            safe_screenshot=safe_screenshot,
            screenshot_path=out_dir / "memberships_screenshot.png",
        )
        result.pop("content", None)
        result["path"] = path
        memberships_best = memberships_best or result
        if result.get("ok"):
            memberships_best = result
            break
    if memberships_best is None:
        memberships_best = {
            "requested_url": _build_site_url(site_origin, MEMBERSHIPS_PAGE_PATH_CANDIDATES[0]),
            "final_url": "",
            "status": 0,
            "ok": False,
            "error": "capture_not_attempted",
            "path": MEMBERSHIPS_PAGE_PATH_CANDIDATES[0],
        }
    memberships_best["artifact"] = "memberships_page.html"
    statuses["memberships"] = memberships_best

    community_result = _capture_page(
        page,
        url=_build_site_url(site_origin, COMMUNITY_PAGE_PATH),
        out_file=out_dir / "community.html",
        safe_screenshot=safe_screenshot,
        screenshot_path=out_dir / "community_screenshot.png",
    )
    community_html = community_result.pop("content", "")
    community_result["path"] = COMMUNITY_PAGE_PATH
    community_result["artifact"] = "community.html"
    statuses["community"] = community_result

    privacy_result = _capture_page(
        page,
        url=_build_site_url(site_origin, PRIVACY_PAGE_PATH),
        out_file=out_dir / "privacy.html",
    )
    privacy_result.pop("content", None)
    privacy_result["path"] = PRIVACY_PAGE_PATH
    privacy_result["artifact"] = "privacy.html"
    statuses["privacy"] = privacy_result

    terms_result = _capture_page(
        page,
        url=_build_site_url(site_origin, TERMS_PAGE_PATH),
        out_file=out_dir / "terms.html",
    )
    terms_result.pop("content", None)
    terms_result["path"] = TERMS_PAGE_PATH
    terms_result["artifact"] = "terms.html"
    statuses["terms"] = terms_result

    if community_html:
        try:
            _write_json(out_dir / "community.json", _extract_community_json(community_html))
            statuses["community"]["community_json_written"] = True
        except Exception:
            statuses["community"]["community_json_written"] = False
    else:
        statuses["community"]["community_json_written"] = False

    memberships_content = ""
    memberships_file = out_dir / "memberships_page.html"
    if memberships_file.exists():
        memberships_content = memberships_file.read_text(errors="replace")
    offer_urls_found = [url for url in REQUIRED_OFFER_URLS if url in memberships_content]

    _atomic_write_json(
        out_dir / "statuses.json",
        {
            "captured_at": _now_iso(),
            "site_origin": site_origin,
            "statuses": statuses,
        },
    )

    return {
        "statuses": statuses,
        "offer_urls_found": offer_urls_found,
        "memberships_page_captured": bool(statuses.get("memberships", {}).get("ok")),
        "community_page_captured": bool(statuses.get("community", {}).get("ok")),
        "privacy_page_captured": bool(statuses.get("privacy", {}).get("ok")),
        "terms_page_captured": bool(statuses.get("terms", {}).get("ok")),
    }


def _extract_products_from_page(page: Any, site_origin: str) -> dict[str, dict[str, str]]:
    products_map: dict[str, dict[str, str]] = {}
    seen: set[str] = set()

    for element in page.query_selector_all('a[href*="/products/"], a[href*="/admin/products/"]'):
        try:
            href = element.get_attribute("href") or ""
            text = (element.inner_text() or "").strip()[:150]
            if not href or href in seen:
                continue
            seen.add(href)
            slug = _extract_slug_from_url(href)
            if not slug or slug == "new":
                continue
            matched = _match_product_name(text, TARGET_PRODUCTS)
            if matched:
                full_url = (
                    href
                    if href.startswith("http")
                    else f"{site_origin}{href}" if href.startswith("/") else f"{site_origin}/admin/products/{slug}"
                )
                products_map[matched] = {"slug": slug, "url": full_url, "display_name": text}
        except Exception:
            pass

    if len(products_map) < len(TARGET_PRODUCTS):
        row_selectors = [
            '[role="row"]',
            "tr",
            '[data-testid*="product"]',
            '[class*="product-row"]',
            '[class*="ProductRow"]',
        ]
        for row_sel in row_selectors:
            if len(products_map) >= len(TARGET_PRODUCTS):
                break
            for row in page.query_selector_all(row_sel):
                try:
                    link = row.query_selector('a[href*="/products/"]')
                    if not link:
                        continue
                    href = link.get_attribute("href") or ""
                    text = (row.inner_text() or link.inner_text() or "").strip()[:150]
                    slug = _extract_slug_from_url(href)
                    if not slug:
                        continue
                    matched = _match_product_name(text, TARGET_PRODUCTS)
                    if matched and matched not in products_map:
                        full_url = (
                            href
                            if href.startswith("http")
                            else f"{site_origin}{href}" if href.startswith("/") else f"{site_origin}/admin/products/{slug}"
                        )
                        products_map[matched] = {"slug": slug, "url": full_url, "display_name": text}
                except Exception:
                    pass

    if len(products_map) < len(TARGET_PRODUCTS):
        for target in TARGET_PRODUCTS:
            if target in products_map:
                continue
            try:
                link = page.get_by_role("link", name=target)
                if link.count() > 0:
                    href = link.first.get_attribute("href") or ""
                    slug = _extract_slug_from_url(href)
                    if slug:
                        full_url = (
                            href
                            if href.startswith("http")
                            else f"{site_origin}{href}" if href.startswith("/") else f"{site_origin}/admin/products/{slug}"
                        )
                        products_map[target] = {"slug": slug, "url": full_url, "display_name": target}
            except Exception:
                pass

    return products_map


def _collect_discover_data(
    *,
    page: Any,
    out_dir: Path,
    captured_at: str,
    site_origin: str,
    safe_screenshot: Any,
    safe_title: Any,
    safe_url: Any,
) -> dict[str, Any]:
    final_url = safe_url(page)
    title = safe_title(page)
    safe_screenshot(page, str(out_dir / "screenshot.png"))
    try:
        from src.playwright_safe import safe_content_excerpt

        (out_dir / "page.html").write_text(safe_content_excerpt(page, 65536) or "(empty)", encoding="utf-8")
    except Exception:
        pass

    products_map = _extract_products_from_page(page, site_origin)
    page_capture = _capture_required_pages(
        page,
        out_dir=out_dir,
        site_origin=site_origin,
        safe_screenshot=safe_screenshot,
    )
    products_output = {name: data.get("slug") or data.get("url", "") for name, data in products_map.items()}
    return {
        "captured_at": captured_at,
        "final_url": final_url,
        "title": title,
        "products_map": products_map,
        "products_output": products_output,
        "page_capture": page_capture,
    }


def _persist_success(
    *,
    out_dir: Path,
    captured_at: str,
    mode: str,
    final_url: str,
    title: str,
    products_output: dict[str, str],
    page_capture: dict[str, Any],
) -> dict[str, Any]:
    debug_doc = {
        "ok": True,
        "status": STATUS_PASS,
        "mode": mode,
        "captured_at": captured_at,
        "final_url": final_url,
        "title": title,
        "logged_in": True,
        "artifact_dir": str(out_dir),
        "products": products_output,
        "product_count": len(products_output),
        "targets_found": list(products_output.keys()),
        "targets_missing": [target for target in TARGET_PRODUCTS if target not in products_output],
        "memberships_page_captured": page_capture["memberships_page_captured"],
        "community_page_captured": page_capture["community_page_captured"],
        "privacy_page_captured": page_capture["privacy_page_captured"],
        "terms_page_captured": page_capture["terms_page_captured"],
        "capture_statuses": page_capture["statuses"],
        "offer_urls_found": page_capture["offer_urls_found"],
        "offer_urls_required": REQUIRED_OFFER_URLS,
    }
    _write_json(out_dir / "debug.json", debug_doc)
    _write_json(out_dir / "products.json", {"products": products_output, "captured_at": captured_at})

    persist_doc = {
        "products": products_output,
        "captured_at": captured_at,
        "artifact_dir": str(out_dir),
    }
    try:
        KAJABI_PRODUCTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        KAJABI_PRODUCTS_PATH.write_text(json.dumps(persist_doc, indent=2), encoding="utf-8")
    except OSError:
        pass

    try:
        from ops.lib.human_gate import clear_gate

        clear_gate(PROJECT_ID)
    except Exception:
        pass

    result = {
        "ok": True,
        "status": STATUS_PASS,
        "mode": mode,
        "artifact_dir": str(out_dir),
        "products_path": str(KAJABI_PRODUCTS_PATH),
        "products_found": list(products_output.keys()),
        "products_missing": [target for target in TARGET_PRODUCTS if target not in products_output],
        "product_count": len(products_output),
        "memberships_page_captured": page_capture["memberships_page_captured"],
        "community_page_captured": page_capture["community_page_captured"],
        "privacy_page_captured": page_capture["privacy_page_captured"],
        "terms_page_captured": page_capture["terms_page_captured"],
        "offer_urls_found": page_capture["offer_urls_found"],
    }
    _write_json(out_dir / "result.json", result)
    _write_latest_pointer(
        out_dir,
        status=STATUS_PASS,
        extra={
            "mode": mode,
            "result_path": str(out_dir / "result.json"),
        },
    )
    _send_transition_alert(
        current_status=STATUS_PASS,
        error_class="DISCOVER_SUCCESS",
        proof_path=str(out_dir / "result.json"),
    )
    return result


def _emit_failure(
    *,
    out_dir: Path,
    error_class: str,
    message: str,
    final_url: str | None = None,
    title: str | None = None,
    mode: str,
) -> int:
    _write_error(
        out_dir,
        error_class,
        mode=mode,
        final_url=final_url or "",
        title=title or "",
        recommended_next_action=message,
        artifact_dir=str(out_dir),
    )
    _write_json(out_dir / "products.json", {"products": {}, "error_class": error_class})
    result = {
        "ok": False,
        "status": STATUS_FAIL,
        "mode": mode,
        "error_class": error_class,
        "artifact_dir": str(out_dir),
        "message": message,
    }
    _write_json(out_dir / "result.json", result)
    _write_latest_pointer(out_dir, status=STATUS_FAIL, error_class=error_class)
    print(json.dumps(result))
    return 1


def _prepare_human_gate(out_dir: Path, reason: str) -> dict[str, Any]:
    root = _repo_root()
    script_dir = root / "ops" / "scripts"
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    from novnc_ready import ensure_novnc_ready_with_recovery
    from ops.lib.aiops_remote_helpers import canonical_novnc_url
    from ops.lib.human_gate import write_gate, write_gate_artifact

    ready, novnc_url, novnc_error_class, journal_artifact = ensure_novnc_ready_with_recovery(out_dir, out_dir.name)
    if not novnc_url:
        base = (
            os.environ.get("OPENCLAW_TS_HOSTNAME", "").strip()
            or os.environ.get("OPENCLAW_TAILSCALE_HOSTNAME", "").strip()
            or "aiops-1.tailc75c62.ts.net"
        )
        novnc_url = canonical_novnc_url(base)
    gate = write_gate(PROJECT_ID, out_dir.name, novnc_url, reason)
    gate_artifact = write_gate_artifact(PROJECT_ID, out_dir.name, gate)
    return {
        "ready": ready,
        "novnc_url": novnc_url,
        "gate_expiry": gate.get("expires_at"),
        "gate_artifact": str(gate_artifact),
        "novnc_error_class": novnc_error_class,
        "journal_artifact": journal_artifact,
    }


def _emit_human_only(
    *,
    out_dir: Path,
    error_class: str,
    message: str,
    mode: str,
    final_url: str | None = None,
    title: str | None = None,
) -> int:
    gate_meta = _prepare_human_gate(out_dir, error_class)
    debug_doc = _write_error(
        out_dir,
        error_class,
        status=STATUS_HUMAN_ONLY,
        mode=mode,
        final_url=final_url or "",
        title=title or "",
        instruction=HUMAN_ONLY_INSTRUCTION,
        novnc_url=gate_meta["novnc_url"],
        gate_expiry=gate_meta["gate_expiry"],
        artifact_dir=str(out_dir),
        message=message,
        novnc_error_class=gate_meta.get("novnc_error_class"),
        journal_artifact=gate_meta.get("journal_artifact"),
    )
    _write_json(out_dir / "products.json", {"products": {}, "error_class": error_class})
    result = {
        "ok": False,
        "status": STATUS_HUMAN_ONLY,
        "mode": mode,
        "error_class": error_class,
        "artifact_dir": str(out_dir),
        "novnc_url": gate_meta["novnc_url"],
        "gate_expiry": gate_meta["gate_expiry"],
        "instruction": HUMAN_ONLY_INSTRUCTION,
        "message": message,
    }
    _write_json(out_dir / "result.json", result)
    _write_json(out_dir / "HUMAN_ONLY.json", result)
    _write_latest_pointer(
        out_dir,
        status=STATUS_HUMAN_ONLY,
        error_class=error_class,
        extra={
            "mode": mode,
            "novnc_url": gate_meta["novnc_url"],
            "gate_expiry": gate_meta["gate_expiry"],
            "instruction": HUMAN_ONLY_INSTRUCTION,
            "gate_artifact": gate_meta["gate_artifact"],
            "result_path": str(out_dir / "result.json"),
        },
    )
    alert = _send_transition_alert(
        current_status=STATUS_HUMAN_ONLY,
        error_class=error_class,
        proof_path=str(out_dir / "result.json"),
        novnc_url=gate_meta["novnc_url"],
        gate_expiry=gate_meta["gate_expiry"],
        instruction=HUMAN_ONLY_INSTRUCTION,
    )
    debug_doc["notification"] = alert
    _write_json(out_dir / "debug.json", debug_doc)
    print(json.dumps(result))
    return 0


def _store_storage_state(context: Any) -> None:
    try:
        storage_state_path = _resolve_storage_state_path()
        storage_state_path.parent.mkdir(parents=True, exist_ok=True)
        context.storage_state(path=str(storage_state_path))
        try:
            storage_state_path.chmod(0o600)
        except OSError:
            pass
    except Exception:
        pass


def _is_profile_lock_error(exc: Exception) -> bool:
    text = str(exc).lower()
    markers = (
        "user data directory is already in use",
        "singletonlock",
        "profile appears to be in use",
        "already in use by another browser",
    )
    return any(marker in text for marker in markers)


def _run_headless(
    *,
    out_dir: Path,
    captured_at: str,
    safe_screenshot: Any,
    safe_title: Any,
    safe_url: Any,
) -> int:
    storage_state = _resolve_storage_state_path()
    if not storage_state.exists() or storage_state.stat().st_size == 0:
        return _emit_failure(
            out_dir=out_dir,
            error_class="KAJABI_STORAGE_STATE_MISSING",
            message=f"Run kajabi_capture_storage_state.py and install to {storage_state}",
            mode=MODE_HEADLESS,
        )

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return _emit_failure(
            out_dir=out_dir,
            error_class="PLAYWRIGHT_NOT_INSTALLED",
            message="pip install playwright && playwright install chromium",
            mode=MODE_HEADLESS,
        )

    root = _repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from services.soma_kajabi.kajabi_admin_context import (
        KAJABI_CLOUDFLARE_BLOCKED,
        ensure_kajabi_soma_admin_context,
    )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(storage_state=str(storage_state))
        page = context.new_page()
        try:
            bootstrap = ensure_kajabi_soma_admin_context(page, artifact_dir=out_dir)
            if not bootstrap.ok:
                error_class = bootstrap.error_class or "KAJABI_ADMIN_404_AFTER_BOOTSTRAP"
                message = bootstrap.recommended_next_action or "Kajabi discover bootstrap failed."
                safe_screenshot(page, str(out_dir / "screenshot.png"))
                try:
                    from src.playwright_safe import safe_content_excerpt

                    (out_dir / "page.html").write_text(
                        safe_content_excerpt(page, 65536) or "(empty)",
                        encoding="utf-8",
                    )
                except Exception:
                    pass
                if error_class == KAJABI_CLOUDFLARE_BLOCKED:
                    return _emit_human_only(
                        out_dir=out_dir,
                        error_class=error_class,
                        message=message,
                        mode=MODE_HEADLESS,
                        final_url=safe_url(page),
                        title=safe_title(page),
                    )
                return _emit_failure(
                    out_dir=out_dir,
                    error_class=error_class,
                    message=message,
                    final_url=safe_url(page),
                    title=safe_title(page),
                    mode=MODE_HEADLESS,
                )

            site_origin = bootstrap.site_origin or DEFAULT_SITE_ORIGIN
            collected = _collect_discover_data(
                page=page,
                out_dir=out_dir,
                captured_at=captured_at,
                site_origin=site_origin,
                safe_screenshot=safe_screenshot,
                safe_title=safe_title,
                safe_url=safe_url,
            )
            result = _persist_success(
                out_dir=out_dir,
                captured_at=captured_at,
                mode=MODE_HEADLESS,
                final_url=collected["final_url"],
                title=collected["title"],
                products_output=collected["products_output"],
                page_capture=collected["page_capture"],
            )
            print(json.dumps(result))
            return 0
        finally:
            try:
                browser.close()
            except Exception:
                pass


def _run_interactive(
    *,
    out_dir: Path,
    captured_at: str,
    safe_screenshot: Any,
    safe_title: Any,
    safe_url: Any,
) -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return _emit_failure(
            out_dir=out_dir,
            error_class="PLAYWRIGHT_NOT_INSTALLED",
            message="pip install playwright && playwright install chromium",
            mode=MODE_INTERACTIVE,
        )

    root = _repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    script_dir = root / "ops" / "scripts"
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))

    from novnc_ready import ensure_novnc_ready_with_recovery, novnc_display
    from services.soma_kajabi.kajabi_admin_context import (
        KAJABI_CLOUDFLARE_BLOCKED,
        KAJABI_SESSION_EXPIRED,
        ensure_kajabi_soma_admin_context,
    )

    ready, _novnc_url, novnc_error_class, journal_artifact = ensure_novnc_ready_with_recovery(out_dir, out_dir.name)
    if not ready:
        return _emit_human_only(
            out_dir=out_dir,
            error_class=novnc_error_class or "NOVNC_NOT_READY",
            message=f"Interactive discover needs a healthy noVNC backend. Journal: {journal_artifact or 'N/A'}",
            mode=MODE_INTERACTIVE,
        )

    env = os.environ.copy()
    env["DISPLAY"] = novnc_display()
    KAJABI_CHROME_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        KAJABI_CHROME_PROFILE_DIR.chmod(0o700)
    except OSError:
        pass

    with sync_playwright() as playwright:
        try:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(KAJABI_CHROME_PROFILE_DIR),
                headless=False,
                env=env,
            )
        except Exception as exc:
            if _is_profile_lock_error(exc):
                return _emit_human_only(
                    out_dir=out_dir,
                    error_class=PROFILE_LOCK_ERROR_CLASS,
                    message="Interactive Kajabi profile is currently in use.",
                    mode=MODE_INTERACTIVE,
                )
            return _emit_failure(
                out_dir=out_dir,
                error_class="KAJABI_INTERACTIVE_LAUNCH_FAILED",
                message=str(exc)[:240],
                mode=MODE_INTERACTIVE,
            )

        try:
            page = context.pages[0] if context.pages else context.new_page()
            bootstrap = ensure_kajabi_soma_admin_context(page, artifact_dir=out_dir)
            if not bootstrap.ok:
                error_class = bootstrap.error_class or "KAJABI_ADMIN_404_AFTER_BOOTSTRAP"
                message = bootstrap.recommended_next_action or "Kajabi discover bootstrap failed."
                safe_screenshot(page, str(out_dir / "screenshot.png"))
                try:
                    from src.playwright_safe import safe_content_excerpt

                    (out_dir / "page.html").write_text(
                        safe_content_excerpt(page, 65536) or "(empty)",
                        encoding="utf-8",
                    )
                except Exception:
                    pass
                if error_class in {KAJABI_CLOUDFLARE_BLOCKED, KAJABI_SESSION_EXPIRED}:
                    return _emit_human_only(
                        out_dir=out_dir,
                        error_class=error_class,
                        message=message,
                        mode=MODE_INTERACTIVE,
                        final_url=safe_url(page),
                        title=safe_title(page),
                    )
                return _emit_failure(
                    out_dir=out_dir,
                    error_class=error_class,
                    message=message,
                    final_url=safe_url(page),
                    title=safe_title(page),
                    mode=MODE_INTERACTIVE,
                )

            site_origin = bootstrap.site_origin or DEFAULT_SITE_ORIGIN
            collected = _collect_discover_data(
                page=page,
                out_dir=out_dir,
                captured_at=captured_at,
                site_origin=site_origin,
                safe_screenshot=safe_screenshot,
                safe_title=safe_title,
                safe_url=safe_url,
            )
            _store_storage_state(context)
            result = _persist_success(
                out_dir=out_dir,
                captured_at=captured_at,
                mode=MODE_INTERACTIVE,
                final_url=collected["final_url"],
                title=collected["title"],
                products_output=collected["products_output"],
                page_capture=collected["page_capture"],
            )
            print(json.dumps(result))
            return 0
        finally:
            try:
                context.close()
            except Exception:
                pass


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    root = _repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    out_dir = _artifact_dir()
    captured_at = _now_iso()
    from src.playwright_safe import safe_screenshot, safe_title, safe_url

    if args.mode == MODE_INTERACTIVE:
        return _run_interactive(
            out_dir=out_dir,
            captured_at=captured_at,
            safe_screenshot=safe_screenshot,
            safe_title=safe_title,
            safe_url=safe_url,
        )
    return _run_headless(
        out_dir=out_dir,
        captured_at=captured_at,
        safe_screenshot=safe_screenshot,
        safe_title=safe_title,
        safe_url=safe_url,
    )


if __name__ == "__main__":
    sys.exit(main())
