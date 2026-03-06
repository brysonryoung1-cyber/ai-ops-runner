#!/usr/bin/env python3
"""Kajabi Discover — Playwright-based discovery of Kajabi admin product identifiers.

Uses the canonical storage_state path from services.soma_kajabi.connector_config.
Bootstraps admin context via Soma site hostname (zane-mccourtney.mykajabi.com) when
app.kajabi.com returns 404. No CDP, no manual steps.

Artifacts: artifacts/soma_kajabi/discover/<run_id>/{products.json, screenshot.png, page.html, debug.json}
Persists: /etc/ai-ops-runner/secrets/soma_kajabi/kajabi_products.json (names + URLs only; NO cookies/tokens)

Error classes:
  KAJABI_STORAGE_STATE_MISSING
  PLAYWRIGHT_NOT_INSTALLED
  KAJABI_NAVIGATION_FAILED
  KAJABI_NOT_LOGGED_IN
  KAJABI_SESSION_EXPIRED — redirect to login; complete login+2FA once
  KAJABI_CLOUDFLARE_BLOCKED — Cloudflare blocking headless browser
  KAJABI_ADMIN_404_AFTER_BOOTSTRAP — all bootstrap attempts 404
  KAJABI_PRODUCTS_PAGE_NO_MATCH — page loaded but target products not found
"""

from __future__ import annotations

import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


KAJABI_PRODUCTS_PATH = Path("/etc/ai-ops-runner/secrets/soma_kajabi/kajabi_products.json")
TARGET_PRODUCTS = ["Home User Library", "Practitioner Library"]
DEFAULT_SITE_ORIGIN = "https://zane-mccourtney.mykajabi.com"
MEMBERSHIPS_PAGE_PATH_CANDIDATES = ["/memberships", "/memberships-soma"]
COMMUNITY_PAGE_PATH = "/community"
PRIVACY_PAGE_PATH = "/privacy-policy"
TERMS_PAGE_PATH = "/terms"
EXPECTED_COMMUNITY_GROUPS = ["Home Users", "Practitioners"]


def _resolve_storage_state_path() -> Path:
    root = _repo_root()
    sys.path.insert(0, str(root))
    from services.soma_kajabi.connector_config import get_storage_state_path, load_soma_kajabi_config
    cfg, _err = load_soma_kajabi_config(root)
    return get_storage_state_path(cfg)


REQUIRED_OFFER_URLS = ["/offers/q6ntyjef/checkout", "/offers/MHMmHyVZ/checkout"]


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
        return Path(env)
    root = _repo_root()
    run_id = f"discover_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"
    out = root / "artifacts" / "soma_kajabi" / "discover" / run_id
    out.mkdir(parents=True, exist_ok=True)
    return out


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _write_latest_pointer(out_dir: Path, status: str, error_class: str | None = None) -> None:
    root = _repo_root()
    pointer = root / "artifacts" / "soma_kajabi" / "discover" / "LATEST.json"
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
    try:
        _atomic_write_json(pointer, payload)
    except Exception:
        pass


def _extract_slug_from_url(href: str) -> str:
    """Extract product slug from URL like .../products/home-user-library or .../products/123."""
    if "/products/" in href:
        parts = href.split("/products/")
        if len(parts) >= 2:
            slug = parts[-1].split("/")[0].split("?")[0].strip()
            if slug and slug != "new":
                return slug
    return ""


def _match_product_name(text: str, targets: list[str]) -> str | None:
    """Case-insensitive partial match. Returns canonical target name or None."""
    text_lower = (text or "").strip().lower()
    for t in targets:
        if t.lower() in text_lower or text_lower in t.lower():
            return t
    return None


def _write_error(out_dir: Path, error_class: str, **kwargs) -> dict:
    doc = {"ok": False, "captured_at": _now_iso(), "error_class": error_class, **kwargs}
    (out_dir / "debug.json").write_text(json.dumps(doc, indent=2))
    return doc


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
    try:
        response = page.goto(url, wait_until="load", timeout=30000)
        page.wait_for_load_state("networkidle", timeout=10000)
        if response is not None:
            status_attr = getattr(response, "status", None)
            if callable(status_attr):
                status_code = int(status_attr())
            elif status_attr is not None:
                status_code = int(status_attr)
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
        "content": content,
    }


def _extract_community_json(html: str) -> dict[str, Any]:
    lower = (html or "").lower()
    name = "Soma Community" if "soma community" in lower else ""
    if not name:
        m = re.search(r"<h1[^>]*>(.*?)</h1>", html or "", flags=re.IGNORECASE | re.DOTALL)
        if m:
            name = re.sub(r"<[^>]+>", " ", m.group(1))
            name = " ".join(name.split())[:120]
    groups = [g for g in EXPECTED_COMMUNITY_GROUPS if g.lower() in lower]
    return {
        "name": name,
        "groups": [{"name": g} for g in groups],
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

    # Memberships capture: try /memberships then /memberships-soma
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
            (out_dir / "community.json").write_text(
                json.dumps(_extract_community_json(community_html), indent=2),
                encoding="utf-8",
            )
            statuses["community"]["community_json_written"] = True
        except Exception:
            statuses["community"]["community_json_written"] = False
    else:
        statuses["community"]["community_json_written"] = False

    memberships_content = ""
    memberships_file = out_dir / "memberships_page.html"
    if memberships_file.exists():
        memberships_content = memberships_file.read_text(errors="replace")
    offer_urls_found = [u for u in REQUIRED_OFFER_URLS if u in memberships_content]

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


def main() -> int:
    root = _repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    out_dir = _artifact_dir()
    captured_at = _now_iso()

    sys.path.insert(0, str(root))
    from src.playwright_safe import safe_content_excerpt, safe_screenshot, safe_title, safe_url

    _storage_state = _resolve_storage_state_path()
    if not _storage_state.exists() or _storage_state.stat().st_size == 0:
        doc = _write_error(
            out_dir,
            "KAJABI_STORAGE_STATE_MISSING",
            recommended_next_action=f"Run kajabi_capture_storage_state.py and install to {_storage_state}",
            artifact_dir=str(out_dir),
        )
        (out_dir / "products.json").write_text(json.dumps({"products": {}, "error_class": doc["error_class"]}, indent=2))
        _write_latest_pointer(out_dir, status="FAIL", error_class="KAJABI_STORAGE_STATE_MISSING")
        print(json.dumps({"ok": False, "error_class": "KAJABI_STORAGE_STATE_MISSING", "artifact_dir": str(out_dir)}))
        return 1

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        doc = _write_error(
            out_dir,
            "PLAYWRIGHT_NOT_INSTALLED",
            recommended_next_action="pip install playwright && playwright install chromium",
            artifact_dir=str(out_dir),
        )
        (out_dir / "products.json").write_text(json.dumps({"products": {}, "error_class": doc["error_class"]}, indent=2))
        _write_latest_pointer(out_dir, status="FAIL", error_class="PLAYWRIGHT_NOT_INSTALLED")
        print(json.dumps({"ok": False, "error_class": "PLAYWRIGHT_NOT_INSTALLED", "artifact_dir": str(out_dir)}))
        return 1

    products_map: dict[str, dict] = {}  # canonical_name -> {slug, url, display_name}
    final_url = ""
    title = ""
    site_origin: str | None = None
    page_capture: dict[str, Any] = {
        "statuses": {},
        "offer_urls_found": [],
        "memberships_page_captured": False,
        "community_page_captured": False,
        "privacy_page_captured": False,
        "terms_page_captured": False,
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=str(_storage_state))
        page = context.new_page()

        try:
            from services.soma_kajabi.kajabi_admin_context import ensure_kajabi_soma_admin_context
            bootstrap = ensure_kajabi_soma_admin_context(page, artifact_dir=out_dir)
        except Exception as e:
            _write_error(
                out_dir,
                "KAJABI_NAVIGATION_FAILED",
                recommended_next_action=f"Check network and storage_state; error: {str(e)[:200]}",
                artifact_dir=str(out_dir),
            )
            safe_screenshot(page, str(out_dir / "screenshot.png"))
            try:
                (out_dir / "page.html").write_text(safe_content_excerpt(page, 65536) or "(empty)", encoding="utf-8")
            except Exception:
                pass
            browser.close()
            (out_dir / "products.json").write_text(json.dumps({"products": {}, "error_class": "KAJABI_NAVIGATION_FAILED"}, indent=2))
            _write_latest_pointer(out_dir, status="FAIL", error_class="KAJABI_NAVIGATION_FAILED")
            print(json.dumps({"ok": False, "error_class": "KAJABI_NAVIGATION_FAILED", "artifact_dir": str(out_dir)}))
            return 1

        if not bootstrap.ok:
            error_class = bootstrap.error_class or "KAJABI_ADMIN_404_AFTER_BOOTSTRAP"
            rec = bootstrap.recommended_next_action or "Complete Kajabi login + 2FA once on aiops-1 capture flow."
            doc = _write_error(
                out_dir,
                error_class,
                final_url=safe_url(page),
                title=safe_title(page),
                logged_in=not bootstrap.login_detected,
                admin_404=bootstrap.admin_404,
                recommended_next_action=rec,
                artifact_dir=str(out_dir),
            )
            safe_screenshot(page, str(out_dir / "screenshot.png"))
            try:
                (out_dir / "page.html").write_text(safe_content_excerpt(page, 65536) or "(empty)", encoding="utf-8")
            except Exception:
                pass
            browser.close()
            (out_dir / "products.json").write_text(json.dumps({"products": {}, "error_class": error_class}, indent=2))
            _write_latest_pointer(out_dir, status="FAIL", error_class=error_class)
            print(json.dumps({"ok": False, "error_class": error_class, "artifact_dir": str(out_dir)}))
            return 1

        site_origin = bootstrap.site_origin or DEFAULT_SITE_ORIGIN
        final_url = safe_url(page)
        title = safe_title(page)

        safe_screenshot(page, str(out_dir / "screenshot.png"))
        try:
            (out_dir / "page.html").write_text(safe_content_excerpt(page, 65536) or "(empty)", encoding="utf-8")
        except Exception:
            pass

        # Extract product links: name → slug/url (page is already on products list from bootstrap)
        # Prefer role/text selectors, fall back to href selectors for resilience
        seen: set[str] = set()

        # Strategy 1: Links containing product hrefs (stable — href structure is Kajabi platform)
        for el in page.query_selector_all('a[href*="/products/"], a[href*="/admin/products/"]'):
            try:
                href = el.get_attribute("href") or ""
                text = (el.inner_text() or "").strip()[:150]
                if not href or href in seen:
                    continue
                seen.add(href)
                slug = _extract_slug_from_url(href)
                if not slug or slug == "new":
                    continue
                matched = _match_product_name(text, TARGET_PRODUCTS)
                if matched:
                    base = site_origin or "https://zane-mccourtney.mykajabi.com"
                    full_url = href if href.startswith("http") else f"{base}{href}" if href.startswith("/") else f"{base}/admin/products/{slug}"
                    products_map[matched] = {"slug": slug, "url": full_url, "display_name": text}
            except Exception:
                pass

        # Strategy 2: Rows with role="row" or table rows (ARIA-first, then structural)
        if len(products_map) < len(TARGET_PRODUCTS):
            row_selectors = [
                '[role="row"]',
                'tr',
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
                            base = site_origin or "https://zane-mccourtney.mykajabi.com"
                            full_url = href if href.startswith("http") else f"{base}{href}" if href.startswith("/") else f"{base}/admin/products/{slug}"
                            products_map[matched] = {"slug": slug, "url": full_url, "display_name": text}
                    except Exception:
                        pass

        # Strategy 3: get_by_role for target product names (Playwright high-level API)
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
                            base = site_origin or "https://zane-mccourtney.mykajabi.com"
                            full_url = href if href.startswith("http") else f"{base}{href}" if href.startswith("/") else f"{base}/admin/products/{slug}"
                            products_map[target] = {"slug": slug, "url": full_url, "display_name": target}
                except Exception:
                    pass

        # Capture required frontdoor pages used by Business DoD checks.
        page_capture = _capture_required_pages(
            page,
            out_dir=out_dir,
            site_origin=site_origin,
            safe_screenshot=safe_screenshot,
        )

        browser.close()

    # 5) Build products.json for artifacts
    products_output: dict[str, str] = {}
    for name, data in products_map.items():
        products_output[name] = data.get("slug") or data.get("url", "")

    debug_doc = {
        "ok": True,
        "captured_at": captured_at,
        "final_url": final_url,
        "title": title,
        "logged_in": True,
        "artifact_dir": str(out_dir),
        "products": products_output,
        "product_count": len(products_output),
        "targets_found": list(products_output.keys()),
        "targets_missing": [t for t in TARGET_PRODUCTS if t not in products_output],
        "memberships_page_captured": page_capture["memberships_page_captured"],
        "community_page_captured": page_capture["community_page_captured"],
        "privacy_page_captured": page_capture["privacy_page_captured"],
        "terms_page_captured": page_capture["terms_page_captured"],
        "capture_statuses": page_capture["statuses"],
        "offer_urls_found": page_capture["offer_urls_found"],
        "offer_urls_required": REQUIRED_OFFER_URLS,
    }
    (out_dir / "debug.json").write_text(json.dumps(debug_doc, indent=2))
    (out_dir / "products.json").write_text(json.dumps({"products": products_output, "captured_at": captured_at}, indent=2))

    # 6) Persist to stable path (safe to store; no cookies/tokens)
    persist_doc = {
        "products": products_output,
        "captured_at": captured_at,
        "artifact_dir": str(out_dir),
    }
    try:
        KAJABI_PRODUCTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        KAJABI_PRODUCTS_PATH.write_text(json.dumps(persist_doc, indent=2))
    except OSError:
        pass  # May lack write permission; artifacts still written

    _write_latest_pointer(out_dir, status="PASS")
    print(json.dumps({
        "ok": True,
        "artifact_dir": str(out_dir),
        "products_path": str(KAJABI_PRODUCTS_PATH),
        "products_found": list(products_output.keys()),
        "products_missing": [t for t in TARGET_PRODUCTS if t not in products_output],
        "product_count": len(products_output),
        "memberships_page_captured": page_capture["memberships_page_captured"],
        "community_page_captured": page_capture["community_page_captured"],
        "privacy_page_captured": page_capture["privacy_page_captured"],
        "terms_page_captured": page_capture["terms_page_captured"],
        "offer_urls_found": page_capture["offer_urls_found"],
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
