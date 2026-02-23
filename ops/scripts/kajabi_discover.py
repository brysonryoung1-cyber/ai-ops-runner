#!/usr/bin/env python3
"""Kajabi Discover — Playwright-based discovery of Kajabi admin product identifiers.

Uses storage_state at /etc/ai-ops-runner/secrets/soma_kajabi/kajabi_storage_state.json.
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
  KAJABI_ADMIN_404_AFTER_BOOTSTRAP — all bootstrap attempts 404
  KAJABI_PRODUCTS_PAGE_NO_MATCH — page loaded but target products not found
"""

from __future__ import annotations

import json
import sys
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path


STORAGE_STATE_PATH = Path("/etc/ai-ops-runner/secrets/soma_kajabi/kajabi_storage_state.json")
KAJABI_PRODUCTS_PATH = Path("/etc/ai-ops-runner/secrets/soma_kajabi/kajabi_products.json")
TARGET_PRODUCTS = ["Home User Library", "Practitioner Library"]


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


def main() -> int:
    root = _repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    out_dir = _artifact_dir()
    captured_at = _now_iso()

    if not STORAGE_STATE_PATH.exists() or STORAGE_STATE_PATH.stat().st_size == 0:
        doc = _write_error(
            out_dir,
            "KAJABI_STORAGE_STATE_MISSING",
            recommended_next_action=f"Run kajabi_capture_storage_state.py and install to {STORAGE_STATE_PATH}",
            artifact_dir=str(out_dir),
        )
        (out_dir / "products.json").write_text(json.dumps({"products": {}, "error_class": doc["error_class"]}, indent=2))
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
        print(json.dumps({"ok": False, "error_class": "PLAYWRIGHT_NOT_INSTALLED", "artifact_dir": str(out_dir)}))
        return 1

    products_map: dict[str, dict] = {}  # canonical_name -> {slug, url, display_name}
    final_url = ""
    title = ""
    site_origin: str | None = None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=str(STORAGE_STATE_PATH))
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
            try:
                page.screenshot(path=str(out_dir / "screenshot.png"))
            except Exception:
                pass
            try:
                (out_dir / "page.html").write_text(page.content(), encoding="utf-8")
            except Exception:
                pass
            browser.close()
            (out_dir / "products.json").write_text(json.dumps({"products": {}, "error_class": "KAJABI_NAVIGATION_FAILED"}, indent=2))
            print(json.dumps({"ok": False, "error_class": "KAJABI_NAVIGATION_FAILED", "artifact_dir": str(out_dir)}))
            return 1

        if not bootstrap.ok:
            error_class = bootstrap.error_class or "KAJABI_ADMIN_404_AFTER_BOOTSTRAP"
            rec = bootstrap.recommended_next_action or "Complete Kajabi login + 2FA once on aiops-1 capture flow."
            doc = _write_error(
                out_dir,
                error_class,
                final_url=page.url,
                title=page.title() or "",
                logged_in=not bootstrap.login_detected,
                admin_404=bootstrap.admin_404,
                recommended_next_action=rec,
                artifact_dir=str(out_dir),
            )
            try:
                page.screenshot(path=str(out_dir / "screenshot.png"))
            except Exception:
                pass
            try:
                (out_dir / "page.html").write_text(page.content(), encoding="utf-8")
            except Exception:
                pass
            browser.close()
            (out_dir / "products.json").write_text(json.dumps({"products": {}, "error_class": error_class}, indent=2))
            print(json.dumps({"ok": False, "error_class": error_class, "artifact_dir": str(out_dir)}))
            return 1

        site_origin = bootstrap.site_origin or "https://zane-mccourtney.mykajabi.com"
        final_url = page.url
        title = page.title() or ""

        try:
            page.screenshot(path=str(out_dir / "screenshot.png"))
        except Exception:
            pass
        try:
            (out_dir / "page.html").write_text(page.content(), encoding="utf-8")
        except Exception:
            pass

        # Extract product links: name → slug/url (page is already on products list from bootstrap)
        seen: set[str] = set()
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

        # Also try table rows / list items with product names
        if len(products_map) < len(TARGET_PRODUCTS):
            for row in page.query_selector_all('tr, [role="row"], [class*="product-row"], [class*="ProductRow"]'):
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

    print(json.dumps({
        "ok": True,
        "artifact_dir": str(out_dir),
        "products_path": str(KAJABI_PRODUCTS_PATH),
        "products_found": list(products_output.keys()),
        "products_missing": [t for t in TARGET_PRODUCTS if t not in products_output],
        "product_count": len(products_output),
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
