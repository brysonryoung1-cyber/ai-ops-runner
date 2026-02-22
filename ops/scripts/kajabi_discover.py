#!/usr/bin/env python3
"""Kajabi Discover — Playwright-based discovery of Kajabi admin/products.

Uses storage_state at /etc/ai-ops-runner/secrets/soma_kajabi/kajabi_storage_state.json.
Opens https://app.kajabi.com, navigates to /admin, captures:
  - discover.json (final_url, title, logged_in, products best-effort)
  - screenshot.png, page.html

If not logged in (redirect to login): ok:false, error_class=KAJABI_NOT_LOGGED_IN.
No secrets printed. Only paths and redacted metadata.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path


STORAGE_STATE_PATH = Path("/etc/ai-ops-runner/secrets/soma_kajabi/kajabi_storage_state.json")
KAJABI_BASE = "https://app.kajabi.com"
KAJABI_ADMIN = "https://app.kajabi.com/admin"
DASHBOARD_INDICATOR = "/admin"
MIN_SNAPSHOT_BYTES = 2048


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
    out = root / "artifacts" / "soma_kajabi" / "kajabi_discover" / run_id
    out.mkdir(parents=True, exist_ok=True)
    return out


def main() -> int:
    out_dir = _artifact_dir()
    captured_at = _now_iso()

    if not STORAGE_STATE_PATH.exists() or STORAGE_STATE_PATH.stat().st_size == 0:
        doc = {
            "ok": False,
            "captured_at": captured_at,
            "error_class": "KAJABI_STORAGE_STATE_MISSING",
            "recommended_next_action": f"Run kajabi_capture_storage_state.py and install to {STORAGE_STATE_PATH}",
            "artifact_dir": str(out_dir),
        }
        (out_dir / "discover.json").write_text(json.dumps(doc, indent=2))
        print(json.dumps({"ok": False, "error_class": "KAJABI_STORAGE_STATE_MISSING", "artifact_dir": str(out_dir)}))
        return 1

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        doc = {
            "ok": False,
            "captured_at": captured_at,
            "error_class": "PLAYWRIGHT_NOT_INSTALLED",
            "recommended_next_action": "pip install playwright && playwright install chromium",
            "artifact_dir": str(out_dir),
        }
        (out_dir / "discover.json").write_text(json.dumps(doc, indent=2))
        print(json.dumps({"ok": False, "error_class": "PLAYWRIGHT_NOT_INSTALLED", "artifact_dir": str(out_dir)}))
        return 1

    final_url = ""
    title = ""
    logged_in = False
    products: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=str(STORAGE_STATE_PATH))
        page = context.new_page()
        try:
            page.goto(KAJABI_BASE, wait_until="domcontentloaded", timeout=30000)
            page.goto(KAJABI_ADMIN, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception as e:
            doc = {
                "ok": False,
                "captured_at": captured_at,
                "error_class": "KAJABI_NAVIGATION_FAILED",
                "recommended_next_action": f"Check network and storage_state; error: {str(e)[:200]}",
                "artifact_dir": str(out_dir),
            }
            (out_dir / "discover.json").write_text(json.dumps(doc, indent=2))
            try:
                page.screenshot(path=str(out_dir / "screenshot.png"))
            except Exception:
                pass
            try:
                (out_dir / "page.html").write_text(page.content(), encoding="utf-8")
            except Exception:
                pass
            browser.close()
            print(json.dumps({"ok": False, "error_class": "KAJABI_NAVIGATION_FAILED", "artifact_dir": str(out_dir)}))
            return 1

        final_url = page.url
        title = page.title() or ""

        logged_in = DASHBOARD_INDICATOR in final_url and "login" not in final_url.lower()

        if not logged_in:
            doc = {
                "ok": False,
                "captured_at": captured_at,
                "final_url": final_url,
                "title": title,
                "logged_in": False,
                "error_class": "KAJABI_NOT_LOGGED_IN",
                "recommended_next_action": "Re-capture storage_state; session expired or redirect to login",
                "artifact_dir": str(out_dir),
                "products": [],
            }
            (out_dir / "discover.json").write_text(json.dumps(doc, indent=2))
            try:
                page.screenshot(path=str(out_dir / "screenshot.png"))
            except Exception:
                pass
            try:
                (out_dir / "page.html").write_text(page.content(), encoding="utf-8")
            except Exception:
                pass
            browser.close()
            print(json.dumps({
                "ok": False,
                "error_class": "KAJABI_NOT_LOGGED_IN",
                "artifact_dir": str(out_dir),
                "final_url": final_url[:80] + "..." if len(final_url) > 80 else final_url,
            }))
            return 1

        try:
            page.screenshot(path=str(out_dir / "screenshot.png"))
        except Exception:
            pass
        try:
            (out_dir / "page.html").write_text(page.content(), encoding="utf-8")
        except Exception:
            pass

        try:
            links = page.query_selector_all('a[href*="/products"], a[href*="/offers"]')
            seen: set[str] = set()
            for el in links[:50]:
                try:
                    href = el.get_attribute("href") or ""
                    text = (el.inner_text() or "").strip()[:100]
                    if href and href not in seen:
                        seen.add(href)
                        products.append({"href": href[:200], "text": text})
                except Exception:
                    pass
        except Exception:
            pass

        browser.close()

    doc = {
        "ok": True,
        "captured_at": captured_at,
        "final_url": final_url,
        "title": title,
        "logged_in": True,
        "artifact_dir": str(out_dir),
        "products": products,
        "product_count": len(products),
    }
    (out_dir / "discover.json").write_text(json.dumps(doc, indent=2))

    print(json.dumps({
        "ok": True,
        "artifact_dir": str(out_dir),
        "final_url": final_url[:80] + "..." if len(final_url) > 80 else final_url,
        "title": title[:50] + "..." if len(title) > 50 else title,
        "logged_in": True,
        "product_count": len(products),
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
