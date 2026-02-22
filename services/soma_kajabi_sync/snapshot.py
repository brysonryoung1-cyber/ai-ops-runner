#!/usr/bin/env python3
"""snapshot_kajabi — Take Kajabi library snapshots into artifacts.

Usage:
    python -m soma_kajabi_sync.snapshot --product "Home User Library"
    python -m soma_kajabi_sync.snapshot --product "Practitioner Library"
    python -m soma_kajabi_sync.snapshot --smoke  # smoke test (no credentials needed)

Produces: artifacts/soma/<run_id>/snapshot.json

If Kajabi API is insufficient, falls back to Playwright browser automation.
Session token must be pre-captured and stored securely.
When storage_state_path is provided, uses Playwright with full storage_state (better for session).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class KajabiSnapshotError(Exception):
    """Raised when snapshot fails with a known error_class."""

    def __init__(self, error_class: str, message: str):
        self.error_class = error_class
        self.message = message
        super().__init__(message)


def _generate_run_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    short = uuid.uuid4().hex[:8]
    return f"snapshot_{ts}_{short}"


def _fetch_kajabi_structure_api(
    product_slug: str, session_token: str
) -> list[dict[str, Any]]:
    """Fetch product structure via Kajabi API (session-auth).

    Uses the Kajabi internal API which requires a valid session cookie.
    Fail-closed: raises on any HTTP error or invalid response.
    """
    import urllib.request
    import urllib.error

    # Kajabi internal API endpoint for product outline
    base_url = "https://app.kajabi.com"
    url = f"{base_url}/api/products/{product_slug}/outline"

    req = urllib.request.Request(url, method="GET")
    req.add_header("Cookie", f"_kjb_session={session_token}")
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "OpenClaw-SomaSync/0.1")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise KajabiSnapshotError(
                "KAJABI_NOT_LOGGED_IN",
                "Kajabi session expired or invalid. Re-capture session token.",
            )
        raise

    # Parse the outline into our canonical category/item structure
    categories = []
    for section in data.get("sections", data.get("categories", [])):
        cat: dict[str, Any] = {
            "name": section.get("title", section.get("name", "Untitled")),
            "slug": section.get("slug", ""),
            "items": [],
        }
        for item in section.get("posts", section.get("items", [])):
            cat["items"].append(
                {
                    "title": item.get("title", "Untitled"),
                    "slug": item.get("slug", ""),
                    "type": item.get("content_type", "unknown"),
                    "published": item.get("published", False),
                    "position": item.get("position", 0),
                }
            )
        categories.append(cat)
    return categories


def _extract_categories_from_page(page: Any, product_slug: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Extract category/item structure from Kajabi admin page DOM.

    Tries multiple selector strategies. Returns (categories, debug_info).
    """
    debug: dict[str, Any] = {
        "selectors_tried": [],
        "sections_matched": 0,
        "items_matched": 0,
        "exceptions": [],
    }
    categories: list[dict[str, Any]] = []

    # Strategy 1: data-testid and known class names
    selector_sets = [
        ('[data-testid="category"], .product-category, .outline-category', '[data-testid="post"], .product-post, .outline-item'),
        ('[data-testid="category"]', '[data-testid="post"]'),
        ('.outline-category, [class*="category"]', '.outline-item, [class*="post"]'),
        ('section, [role="region"]', 'li, [role="listitem"], [class*="lesson"], [class*="post"]'),
    ]

    for section_sel, item_sel in selector_sets:
        try:
            sections = page.query_selector_all(section_sel)
            debug["selectors_tried"].append({"section": section_sel, "item": item_sel, "count": len(sections)})
            if not sections:
                continue
            for section_el in sections:
                cat_name = ""
                for title_sel in ["h3", ".category-title", "[data-testid='category-title']", "h2", "h4"]:
                    name_el = section_el.query_selector(title_sel)
                    if name_el:
                        cat_name = (name_el.inner_text() or "").strip()
                        if cat_name:
                            break

                items = []
                item_els = section_el.query_selector_all(item_sel)
                for item_el in item_els:
                    title = "Untitled"
                    for tsel in [".post-title", "[data-testid='post-title']", "span", "a"]:
                        title_el = item_el.query_selector(tsel)
                        if title_el:
                            raw = (title_el.inner_text() or "").strip()
                            if raw and len(raw) < 200:
                                title = raw
                                break
                    items.append({
                        "title": title,
                        "slug": "",
                        "type": "unknown",
                        "published": True,
                        "position": len(items),
                    })

                if cat_name or items:
                    categories.append({"name": cat_name or "Untitled", "slug": "", "items": items})
            if categories:
                debug["sections_matched"] = len(sections)
                debug["items_matched"] = sum(len(c.get("items", [])) for c in categories)
                break
        except Exception as e:
            debug["exceptions"].append(f"{section_sel}: {str(e)[:100]}")

    return categories, debug


def _fetch_kajabi_structure_playwright(
    product_slug: str,
    session_token: str | None = None,
    storage_state_path: str | Path | None = None,
    debug_artifact_dir: Path | None = None,
    product_name: str = "",
) -> list[dict[str, Any]]:
    """Fallback: use Playwright to scrape Kajabi product structure.

    Requires playwright to be installed. Uses either session_token (cookie injection)
    or storage_state_path (full session). When debug_artifact_dir is set, writes
    screenshot, page.html, and debug.json for diagnosis.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "ERROR: Playwright not installed. "
            "Install with: pip install playwright && playwright install chromium",
            file=sys.stderr,
        )
        sys.exit(1)

    categories: list[dict[str, Any]] = []
    debug_data: dict[str, Any] = {
        "product_slug": product_slug,
        "product_name": product_name,
        "method": "storage_state" if storage_state_path else "cookie",
        "timings": {},
        "final_url": "",
        "page_title": "",
        "logged_in": True,
        "selectors_matched": {},
        "categories_count": 0,
        "items_count": 0,
        "exceptions": [],
    }
    t0 = time.monotonic()

    with sync_playwright() as p:
        launch_opts: dict[str, Any] = {"headless": True}
        pw_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
        if pw_path:
            launch_opts["env"] = {**os.environ, "PLAYWRIGHT_BROWSERS_PATH": pw_path}
        browser = p.chromium.launch(**launch_opts)

        if storage_state_path and Path(storage_state_path).exists():
            context = browser.new_context(storage_state=str(storage_state_path))
        else:
            context = browser.new_context()
            if session_token:
                context.add_cookies([
                    {
                        "name": "_kjb_session",
                        "value": session_token,
                        "domain": ".kajabi.com",
                        "path": "/",
                        "httpOnly": True,
                        "secure": True,
                        "sameSite": "Lax",
                    }
                ])

        page = context.new_page()
        # product_slug may be full URL (from discover) or slug
        if product_slug.startswith("http"):
            url = product_slug
        else:
            url = f"https://app.kajabi.com/admin/products/{product_slug}"
        debug_data["target_url"] = url

        resp = None
        try:
            resp = page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception as e:
            debug_data["exceptions"].append(f"navigation: {str(e)[:200]}")
            debug_data["timings"]["navigation_ms"] = int((time.monotonic() - t0) * 1000)
            if debug_artifact_dir:
                _write_playwright_debug_artifacts(page, debug_artifact_dir, product_slug[:50], debug_data)
            browser.close()
            raise

        debug_data["timings"]["navigation_ms"] = int((time.monotonic() - t0) * 1000)
        debug_data["final_url"] = page.url
        debug_data["page_title"] = page.title() or ""

        # Detect 404
        if resp and resp.status == 404:
            if debug_artifact_dir:
                _write_playwright_debug_artifacts(page, debug_artifact_dir, (product_slug or "unknown").replace("/", "_")[:50], debug_data)
            browser.close()
            raise KajabiSnapshotError(
                "KAJABI_PRODUCT_NOT_FOUND",
                f"Product URL returned 404. Run soma_kajabi_discover to refresh product mapping.",
            )

        # Detect login redirect
        if "sign_in" in page.url or "login" in page.url.lower():
            debug_data["logged_in"] = False
            if debug_artifact_dir:
                _write_playwright_debug_artifacts(page, debug_artifact_dir, product_slug, debug_data)
            browser.close()
            raise KajabiSnapshotError(
                "KAJABI_NOT_LOGGED_IN",
                "Redirected to login; session expired. Re-capture storage_state or session token.",
            )

        t1 = time.monotonic()
        categories, extract_debug = _extract_categories_from_page(page, product_slug)
        debug_data["timings"]["extract_ms"] = int((time.monotonic() - t1) * 1000)
        debug_data["selectors_matched"] = extract_debug
        debug_data["categories_count"] = len(categories)
        debug_data["items_count"] = sum(len(c.get("items", [])) for c in categories)

        if debug_artifact_dir:
            _write_playwright_debug_artifacts(page, debug_artifact_dir, product_slug, debug_data)

        browser.close()

    return categories


def _write_playwright_debug_artifacts(
    page: Any, out_dir: Path, product_slug: str, debug_data: dict[str, Any]
) -> None:
    """Write screenshot, page.html, and debug.json to out_dir."""
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_slug = product_slug.replace("/", "_")[:50]
    try:
        page.screenshot(path=str(out_dir / f"kajabi_{safe_slug}_screenshot.png"))
    except Exception:
        pass
    try:
        (out_dir / f"kajabi_{safe_slug}_page.html").write_text(page.content(), encoding="utf-8")
    except Exception:
        pass
    (out_dir / f"kajabi_{safe_slug}_debug.json").write_text(
        json.dumps(debug_data, indent=2), encoding="utf-8"
    )


def _validate_storage_state_has_kajabi_cookies(path: Path) -> tuple[bool, str]:
    """Check storage_state contains cookies for app.kajabi.com or .kajabi.com."""
    if not path.exists() or path.stat().st_size == 0:
        return False, "storage_state file missing or empty"
    try:
        data = json.loads(path.read_text())
        cookies = data.get("cookies") if isinstance(data, dict) else []
        if not isinstance(cookies, list):
            return False, "storage_state cookies not a list"
        domains = set()
        has_kajabi = False
        for c in cookies:
            if isinstance(c, dict):
                dom = c.get("domain", "")
                if dom:
                    domains.add(dom)
                if "kajabi.com" in dom:
                    has_kajabi = True
        if not has_kajabi:
            return False, f"storage_state has no cookies for app.kajabi.com (domains: {list(domains)[:5]})"
        return True, "ok"
    except Exception as e:
        return False, f"storage_state invalid: {str(e)[:100]}"


def snapshot_kajabi(
    product_name: str,
    smoke: bool = False,
    storage_state_path: str | Path | None = None,
    debug_artifact_dir: Path | None = None,
) -> dict[str, Any]:
    """Main entrypoint: take a Kajabi product snapshot.

    Returns a result dict with status and artifact paths.
    When storage_state_path is provided, uses it for Playwright (preferred over cookie).
    When debug_artifact_dir is set, writes screenshot, page.html, debug.json on Playwright path.
    """
    from .artifacts import write_run_manifest, write_snapshot_json
    from .config import (
        KAJABI_PRODUCTS,
        get_artifacts_dir,
        load_kajabi_products,
        load_secret,
        mask_secret,
    )

    products_map = load_kajabi_products()
    if product_name not in products_map and product_name not in KAJABI_PRODUCTS:
        print(
            f"ERROR: Unknown product '{product_name}'. "
            f"Known products: {', '.join(set(KAJABI_PRODUCTS.keys()) | set(products_map.keys()))}",
            file=sys.stderr,
        )
        sys.exit(1)

    product_slug = products_map.get(product_name) or KAJABI_PRODUCTS.get(product_name, "")
    run_id = _generate_run_id()
    out_dir = get_artifacts_dir(run_id)

    print(f"=== snapshot_kajabi ===")
    print(f"  Product:  {product_name} ({product_slug})")
    print(f"  Run ID:   {run_id}")
    print(f"  Out dir:  {out_dir}")
    print()

    if smoke:
        # Smoke test: write synthetic data to verify artifact pipeline
        print("  [SMOKE MODE] Using synthetic data (no credentials required)")
        categories = [
            {
                "name": "Sample Category",
                "slug": "sample-category",
                "items": [
                    {
                        "title": "Sample Video 1",
                        "slug": "sample-video-1",
                        "type": "video",
                        "published": True,
                        "position": 0,
                    },
                    {
                        "title": "Sample PDF",
                        "slug": "sample-pdf",
                        "type": "pdf",
                        "published": True,
                        "position": 1,
                    },
                ],
            },
        ]
    else:
        session_token: str | None = None
        storage_path: Path | None = None
        if storage_state_path and Path(storage_state_path).exists():
            storage_path = Path(storage_state_path)
            print(f"  Auth:     storage_state at {storage_path}")
        else:
            session_token = load_secret("KAJABI_SESSION_TOKEN", required=False)
            if session_token:
                print(f"  Session:  {mask_secret(session_token)}")
            else:
                print("  Session:  (none; will use storage_state if provided)")

        if not session_token and not storage_path:
            raise KajabiSnapshotError(
                "KAJABI_STORAGE_STATE_INVALID",
                "Neither KAJABI_SESSION_TOKEN nor valid storage_state_path provided.",
            )

        # Try API first (only when we have session token; API uses cookie)
        if session_token:
            try:
                print("  Method:   API")
                categories = _fetch_kajabi_structure_api(product_slug, session_token)
            except KajabiSnapshotError:
                raise
            except Exception as api_err:
                print(f"  API failed ({api_err}), falling back to Playwright...")
                categories = _fetch_kajabi_structure_playwright(
                    product_slug,
                    session_token=session_token,
                    storage_state_path=str(storage_path) if storage_path else None,
                    debug_artifact_dir=debug_artifact_dir,
                    product_name=product_name,
                )
        else:
            print("  Method:   Playwright (storage_state)")
            categories = _fetch_kajabi_structure_playwright(
                product_slug,
                session_token=None,
                storage_state_path=str(storage_path) if storage_path else None,
                debug_artifact_dir=debug_artifact_dir,
                product_name=product_name,
            )

    # Write artifact
    snap_path = write_snapshot_json(out_dir, product_name, categories)
    print(f"\n  Snapshot: {snap_path}")
    print(
        f"  Categories: {len(categories)}, "
        f"Items: {sum(len(c.get('items', [])) for c in categories)}"
    )

    artifacts_written = ["snapshot.json", "snapshot.json.sha256"]
    status = "success"
    write_run_manifest(out_dir, run_id, "snapshot_kajabi", status, artifacts_written)

    result = {
        "status": status,
        "run_id": run_id,
        "product": product_name,
        "artifacts_dir": str(out_dir),
        "artifacts": artifacts_written,
        "total_categories": len(categories),
        "total_items": sum(len(c.get("items", [])) for c in categories),
    }
    print(f"\n  Result: {json.dumps(result, indent=2)}")
    return result


def main() -> None:
    from .config import KAJABI_PRODUCTS

    parser = argparse.ArgumentParser(
        description="Take a Kajabi library snapshot into artifacts"
    )
    parser.add_argument(
        "--product",
        choices=list(KAJABI_PRODUCTS.keys()),
        help="Kajabi product to snapshot",
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="Smoke test mode — synthetic data, no credentials needed",
    )
    parser.add_argument(
        "--storage-state",
        metavar="PATH",
        help="Path to Playwright storage_state JSON (overrides KAJABI_SESSION_TOKEN)",
    )
    parser.add_argument(
        "--debug-dir",
        metavar="PATH",
        help="Write debug artifacts (screenshot, page.html, debug.json) to this dir",
    )
    args = parser.parse_args()

    if not args.smoke and not args.product:
        parser.error("--product is required (unless --smoke)")

    product = args.product or "Home User Library"
    storage_path = Path(args.storage_state) if args.storage_state else None
    debug_dir = Path(args.debug_dir) if args.debug_dir else None
    snapshot_kajabi(product, smoke=args.smoke, storage_state_path=storage_path, debug_artifact_dir=debug_dir)


if __name__ == "__main__":
    main()
