#!/usr/bin/env python3
"""snapshot_kajabi — Take Kajabi library snapshots into artifacts.

Usage:
    python -m soma_kajabi_sync.snapshot --product "Home User Library"
    python -m soma_kajabi_sync.snapshot --product "Practitioner Library"
    python -m soma_kajabi_sync.snapshot --smoke  # smoke test (no credentials needed)

Produces: artifacts/soma/<run_id>/snapshot.json

If Kajabi API is insufficient, falls back to Playwright browser automation.
Session token must be pre-captured and stored securely.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .artifacts import write_run_manifest, write_snapshot_json
from .config import (
    KAJABI_PRODUCTS,
    get_artifacts_dir,
    load_secret,
    mask_secret,
)


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
            print(
                "ERROR: Kajabi session expired or invalid. "
                "Re-capture session token.",
                file=sys.stderr,
            )
            sys.exit(1)
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


def _fetch_kajabi_structure_playwright(
    product_slug: str, session_token: str
) -> list[dict[str, Any]]:
    """Fallback: use Playwright to scrape Kajabi product structure.

    Requires playwright to be installed in the container.
    One-time operator login session capture documented in SOMA_KAJABI_RUNBOOK.md.
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

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        # Inject session cookie
        context.add_cookies(
            [
                {
                    "name": "_kjb_session",
                    "value": session_token,
                    "domain": ".kajabi.com",
                    "path": "/",
                    "httpOnly": True,
                    "secure": True,
                    "sameSite": "Lax",
                }
            ]
        )
        page = context.new_page()

        # Navigate to product admin page
        page.goto(
            f"https://app.kajabi.com/admin/products/{product_slug}",
            wait_until="networkidle",
            timeout=30000,
        )

        # Check for auth failure
        if "sign_in" in page.url:
            browser.close()
            print(
                "ERROR: Kajabi session expired (redirected to login). "
                "Re-capture session token.",
                file=sys.stderr,
            )
            sys.exit(1)

        # Extract category/item structure from the admin page DOM
        sections = page.query_selector_all(
            '[data-testid="category"], .product-category, .outline-category'
        )
        for section_el in sections:
            cat_name = ""
            name_el = section_el.query_selector(
                "h3, .category-title, [data-testid='category-title']"
            )
            if name_el:
                cat_name = name_el.inner_text().strip()

            items = []
            item_els = section_el.query_selector_all(
                '[data-testid="post"], .product-post, .outline-item'
            )
            for item_el in item_els:
                title_el = item_el.query_selector(
                    ".post-title, [data-testid='post-title'], span"
                )
                title = title_el.inner_text().strip() if title_el else "Untitled"
                items.append(
                    {
                        "title": title,
                        "slug": "",
                        "type": "unknown",
                        "published": True,
                        "position": len(items),
                    }
                )

            if cat_name or items:
                categories.append(
                    {"name": cat_name or "Untitled", "slug": "", "items": items}
                )

        browser.close()

    return categories


def snapshot_kajabi(product_name: str, smoke: bool = False) -> dict[str, Any]:
    """Main entrypoint: take a Kajabi product snapshot.

    Returns a result dict with status and artifact paths.
    """
    if product_name not in KAJABI_PRODUCTS:
        print(
            f"ERROR: Unknown product '{product_name}'. "
            f"Known products: {', '.join(KAJABI_PRODUCTS.keys())}",
            file=sys.stderr,
        )
        sys.exit(1)

    product_slug = KAJABI_PRODUCTS[product_name]
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
        # Load session token (fail-closed)
        session_token = load_secret("KAJABI_SESSION_TOKEN")
        assert session_token is not None
        print(f"  Session:  {mask_secret(session_token)}")

        # Try API first, fall back to Playwright
        try:
            print("  Method:   API")
            categories = _fetch_kajabi_structure_api(product_slug, session_token)
        except Exception as api_err:
            print(f"  API failed ({api_err}), falling back to Playwright...")
            categories = _fetch_kajabi_structure_playwright(
                product_slug, session_token
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
    args = parser.parse_args()

    if not args.smoke and not args.product:
        parser.error("--product is required (unless --smoke)")

    product = args.product or "Home User Library"
    snapshot_kajabi(product, smoke=args.smoke)


if __name__ == "__main__":
    main()
