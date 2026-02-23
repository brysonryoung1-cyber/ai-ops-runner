#!/usr/bin/env python3
"""ensure_kajabi_soma_admin_context — Bootstrap Soma site admin context without CDP/user actions.

Shared helper used by:
  - soma_kajabi_discover (ops/scripts/kajabi_discover.py)
  - soma_kajabi_snapshot_debug (services.soma_kajabi.snapshot_debug_runner)
  - soma_kajabi_phase0 snapshot step (via services.soma_kajabi_sync.snapshot)

Navigates through a chain of URLs to reach the Soma products page when app.kajabi.com
returns 404 (wrong site context). No CDP, no manual steps.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Canonical URLs
SOMA_SITE = "https://zane-mccourtney.mykajabi.com"
SOMA_ADMIN = f"{SOMA_SITE}/admin"
SOMA_PRODUCTS = f"{SOMA_SITE}/admin/products"
KAJABI_ADMIN = "https://app.kajabi.com/admin"
KAJABI_PRODUCTS = "https://app.kajabi.com/admin/products"
KAJABI_SITES = "https://app.kajabi.com/admin/sites"

TARGET_PRODUCTS = ["Home User Library", "Practitioner Library"]

# Error classes (replace KAJABI_WRONG_SITE_OR_PERMISSIONS)
KAJABI_SESSION_EXPIRED = "KAJABI_SESSION_EXPIRED"
KAJABI_CLOUDFLARE_BLOCKED = "KAJABI_CLOUDFLARE_BLOCKED"
KAJABI_ADMIN_404_AFTER_BOOTSTRAP = "KAJABI_ADMIN_404_AFTER_BOOTSTRAP"
KAJABI_PRODUCTS_PAGE_NO_MATCH = "KAJABI_PRODUCTS_PAGE_NO_MATCH"


@dataclass
class BootstrapAttempt:
    url_requested: str
    final_url: str
    title: str
    admin_404_detected: bool
    login_detected: bool
    screenshot_path: str | None = None
    html_excerpt: str | None = None


@dataclass
class BootstrapResult:
    ok: bool
    error_class: str | None = None
    recommended_next_action: str | None = None
    admin_404: bool = False
    login_detected: bool = False
    products_found: list[str] = field(default_factory=list)
    products_missing: list[str] = field(default_factory=list)
    site_origin: str | None = None  # e.g. https://zane-mccourtney.mykajabi.com
    attempts: list[dict[str, Any]] = field(default_factory=list)
    artifact_path: str | None = None


def _is_cloudflare_blocked(content: str) -> bool:
    """Detect Cloudflare block (not session expiry)."""
    content_lower = (content or "").lower()[:8192]
    return "cloudflare" in content_lower and ("blocked" in content_lower or "attention required" in content_lower)


def _is_login_page(url: str, content: str) -> bool:
    """Detect login/sign-in page. Excludes Cloudflare block (check first)."""
    if _is_cloudflare_blocked(content or ""):
        return False  # Cloudflare block, not login
    url_lower = url.lower()
    if "/login" in url_lower or "sign_in" in url_lower or "sign-in" in url_lower:
        return True
    content_lower = (content or "").lower()[:4096]
    if "sign in" in content_lower or "log in" in content_lower:
        return True
    return False


def _is_404_page(title: str, content: str) -> bool:
    """Detect 404 page heuristically."""
    title_lower = (title or "").lower()
    if "404" in title_lower or "doesn't exist" in title_lower or "not found" in title_lower:
        return True
    content_lower = (content or "")[:2048].lower()
    if "404" in content_lower or "doesn't exist" in content_lower:
        return True
    return False


def _page_has_products(content: str, targets: list[str]) -> tuple[bool, list[str], list[str]]:
    """Check if page contains target product names. Returns (has_any, found, missing)."""
    content_lower = (content or "").lower()
    found: list[str] = []
    for t in targets:
        if t.lower() in content_lower:
            found.append(t)
    missing = [x for x in targets if x not in found]
    return len(found) > 0, found, missing


def _try_click_soma_site(page: Any) -> None:
    """Try to click the Soma site in the site picker. No-op if not found."""
    selectors = [
        'a:has-text("Soma")',
        'a:has-text("zane-mccourtney")',
        'button:has-text("Soma")',
        '[role="link"]:has-text("Soma")',
        '[data-testid*="site"]:has-text("Soma")',
        'text=Soma',
        'text=zane-mccourtney',
        'text="Soma"',
        '[href*="soma"], [href*="zane-mccourtney"]',
        'div:has-text("Soma") >> a',
        'tr:has-text("Soma") >> a',
    ]
    for sel in selectors:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.click()
                page.wait_for_load_state("load", timeout=15000)
                return
        except Exception:
            pass
    # Try get_by_role / get_by_text (Playwright 1.20+)
    try:
        link = page.get_by_role("link", name="Soma")
        if link.count() > 0:
            link.first.click()
            page.wait_for_load_state("load", timeout=15000)
    except Exception:
        pass


def _try_navigate(page: Any, url: str, timeout: int = 30000) -> tuple[str, str, bool, bool]:
    """Navigate to url, return (final_url, title, admin_404, login_detected)."""
    try:
        resp = page.goto(url, wait_until="load", timeout=timeout)
        final_url = page.url
        title = page.title() or ""
        content = page.content()[:4096] if hasattr(page, "content") else ""

        admin_404 = (resp is not None and resp.status == 404) or _is_404_page(title, content)
        login_detected = _is_login_page(final_url, content)
        return final_url, title, admin_404, login_detected
    except Exception:
        return page.url, page.title() or "", True, False


def _write_bootstrap_artifact(
    artifact_dir: Path,
    attempts: list[dict],
    error_class: str | None,
    recommended_next_action: str | None,
) -> Path:
    """Write bootstrap_failure.json artifact."""
    doc = {
        "attempts": attempts,
        "error_class": error_class,
        "recommended_next_action": recommended_next_action,
    }
    path = artifact_dir / "bootstrap_failure.json"
    path.write_text(json.dumps(doc, indent=2))
    return path


def ensure_kajabi_soma_admin_context(
    page: Any,
    artifact_dir: Path | None = None,
) -> BootstrapResult:
    """Bootstrap admin context: navigate to Soma products page.

    Attempt chain:
      1) goto SOMA_PRODUCTS
      2) if 404, goto SOMA_ADMIN then SOMA_PRODUCTS
      3) if still 404, goto KAJABI_ADMIN then KAJABI_PRODUCTS
      4) if still 404, goto KAJABI_SITES, select Soma site, retry KAJABI_PRODUCTS

    Returns BootstrapResult with ok, error_class, site_origin, etc.
    """
    attempts: list[dict[str, Any]] = []
    out_dir = artifact_dir or Path.cwd()

    def _capture_attempt(
        url_req: str,
        final_url: str,
        title: str,
        admin_404: bool,
        login_detected: bool,
        screenshot_path: str | None = None,
        html_excerpt: str | None = None,
    ) -> None:
        attempts.append({
            "url_requested": url_req,
            "final_url": final_url,
            "title": title,
            "admin_404_detected": admin_404,
            "login_detected": login_detected,
            "screenshot_path": screenshot_path,
            "html_excerpt": (html_excerpt or "")[:2048] if html_excerpt else None,
        })

    def _screenshot_and_html(prefix: str = "bootstrap") -> tuple[str | None, str | None]:
        path = None
        html = None
        try:
            p = out_dir / f"{prefix}_screenshot.png"
            page.screenshot(path=str(p))
            path = str(p)
        except Exception:
            pass
        try:
            html = page.content()[:2048] if hasattr(page, "content") else ""
        except Exception:
            pass
        return path, html

    # --- Attempt 1: KAJABI_ADMIN (dashboard) then KAJABI_SITES (site picker) — admin is only at app.kajabi.com ---
    # Land on dashboard first to establish session, then site picker to switch to Soma.
    _try_navigate(page, KAJABI_ADMIN)
    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except Exception:
        page.wait_for_load_state("load", timeout=5000)
    final_url, title, admin_404, login_detected = _try_navigate(page, KAJABI_SITES)
    screenshot_path, html_excerpt = _screenshot_and_html("attempt1_kajabi_sites")
    _capture_attempt(KAJABI_SITES, final_url, title, admin_404, login_detected, screenshot_path, html_excerpt)

    content_check = (html_excerpt or "") + (page.content()[:8192] if hasattr(page, "content") else "")
    if _is_cloudflare_blocked(content_check):
        _write_bootstrap_artifact(
            out_dir, attempts, KAJABI_CLOUDFLARE_BLOCKED,
            "Cloudflare blocking headless browser. Run capture on machine with human browser, or use headed mode.",
        )
        return BootstrapResult(
            ok=False,
            error_class=KAJABI_CLOUDFLARE_BLOCKED,
            recommended_next_action="Cloudflare blocking headless browser. Run capture on machine with human browser, or use headed mode.",
            attempts=attempts,
            artifact_path=str(out_dir / "bootstrap_failure.json"),
        )

    if login_detected:
        content_for_check = (html_excerpt or "") + (page.content()[:8192] if hasattr(page, "content") else "")
        if _is_cloudflare_blocked(content_for_check):
            _write_bootstrap_artifact(
                out_dir, attempts, KAJABI_CLOUDFLARE_BLOCKED,
                "Cloudflare blocking headless browser. Run capture on machine with human browser, or use headed mode.",
            )
            return BootstrapResult(
                ok=False,
                error_class=KAJABI_CLOUDFLARE_BLOCKED,
                recommended_next_action="Cloudflare blocking headless browser. Run capture on machine with human browser, or use headed mode.",
                login_detected=False,
                attempts=attempts,
                artifact_path=str(out_dir / "bootstrap_failure.json"),
            )
        _write_bootstrap_artifact(
            out_dir, attempts, KAJABI_SESSION_EXPIRED,
            "Complete Kajabi login + 2FA once on aiops-1 capture flow.",
        )
        return BootstrapResult(
            ok=False,
            error_class=KAJABI_SESSION_EXPIRED,
            recommended_next_action="Complete Kajabi login + 2FA once on aiops-1 capture flow.",
            login_detected=True,
            attempts=attempts,
            artifact_path=str(out_dir / "bootstrap_failure.json"),
        )

    if not admin_404:
        # Sites page loaded — try to select Soma site, then go to products
        _try_click_soma_site(page)
        final_url, title, admin_404, login_detected = _try_navigate(page, KAJABI_PRODUCTS)
        screenshot_path, html_excerpt = _screenshot_and_html("attempt1_sites_then_products")
        _capture_attempt(KAJABI_PRODUCTS, final_url, title, admin_404, login_detected, screenshot_path, html_excerpt)
        if login_detected:
            _write_bootstrap_artifact(out_dir, attempts, KAJABI_SESSION_EXPIRED,
                                     "Complete Kajabi login + 2FA once on aiops-1 capture flow.")
            return BootstrapResult(ok=False, error_class=KAJABI_SESSION_EXPIRED,
                                  recommended_next_action="Complete Kajabi login + 2FA once on aiops-1 capture flow.",
                                  login_detected=True, attempts=attempts,
                                  artifact_path=str(out_dir / "bootstrap_failure.json"))
        if not admin_404:
            content = (html_excerpt or "") + (page.content()[:8192] if hasattr(page, "content") else "")
            has_any, found, missing = _page_has_products(content, TARGET_PRODUCTS)
            if has_any:
                origin = final_url.split("/admin")[0] if "/admin" in final_url else "https://app.kajabi.com"
                return BootstrapResult(ok=True, admin_404=False, products_found=found, products_missing=missing,
                                      site_origin=origin, attempts=attempts)
            _write_bootstrap_artifact(out_dir, attempts, KAJABI_PRODUCTS_PAGE_NO_MATCH,
                                     "Products page loaded but target products not found. Check site.")
            return BootstrapResult(ok=False, error_class=KAJABI_PRODUCTS_PAGE_NO_MATCH,
                                  recommended_next_action="Products page loaded but target products not found.",
                                  admin_404=False, products_found=[], products_missing=TARGET_PRODUCTS,
                                  attempts=attempts, artifact_path=str(out_dir / "bootstrap_failure.json"))

    # --- Attempt 2: SOMA_PRODUCTS (mykajabi.com — storefront; /admin may 404) ---
    final_url, title, admin_404, login_detected = _try_navigate(page, SOMA_PRODUCTS)
    screenshot_path, html_excerpt = _screenshot_and_html("attempt2_soma_products")
    _capture_attempt(SOMA_PRODUCTS, final_url, title, admin_404, login_detected, screenshot_path, html_excerpt)

    if login_detected:
        _write_bootstrap_artifact(out_dir, attempts, KAJABI_SESSION_EXPIRED,
                                 "Complete Kajabi login + 2FA once on aiops-1 capture flow.")
        return BootstrapResult(ok=False, error_class=KAJABI_SESSION_EXPIRED,
                              recommended_next_action="Complete Kajabi login + 2FA once on aiops-1 capture flow.",
                              login_detected=True, attempts=attempts,
                              artifact_path=str(out_dir / "bootstrap_failure.json"))

    if not admin_404:
        content = (html_excerpt or "") + (page.content()[:8192] if hasattr(page, "content") else "")
        has_any, found, missing = _page_has_products(content, TARGET_PRODUCTS)
        if has_any:
            origin = final_url.split("/admin")[0] if "/admin" in final_url else SOMA_SITE
            return BootstrapResult(ok=True, admin_404=False, products_found=found, products_missing=missing,
                                  site_origin=origin, attempts=attempts)
        _write_bootstrap_artifact(out_dir, attempts, KAJABI_PRODUCTS_PAGE_NO_MATCH,
                                 "Products page loaded but target products not found. Check site.")
        return BootstrapResult(ok=False, error_class=KAJABI_PRODUCTS_PAGE_NO_MATCH,
                              recommended_next_action="Products page loaded but target products not found.",
                              admin_404=False, products_found=found, products_missing=missing,
                              attempts=attempts, artifact_path=str(out_dir / "bootstrap_failure.json"))

    # --- Attempt 3: KAJABI_ADMIN then KAJABI_PRODUCTS (direct) ---
    _try_navigate(page, KAJABI_ADMIN)
    final_url, title, admin_404, login_detected = _try_navigate(page, KAJABI_PRODUCTS)
    screenshot_path, html_excerpt = _screenshot_and_html("attempt3_kajabi_products")
    _capture_attempt(KAJABI_PRODUCTS, final_url, title, admin_404, login_detected, screenshot_path, html_excerpt)

    if login_detected:
        _write_bootstrap_artifact(out_dir, attempts, KAJABI_SESSION_EXPIRED,
                                 "Complete Kajabi login + 2FA once on aiops-1 capture flow.")
        return BootstrapResult(ok=False, error_class=KAJABI_SESSION_EXPIRED,
                              recommended_next_action="Complete Kajabi login + 2FA once on aiops-1 capture flow.",
                              login_detected=True, attempts=attempts,
                              artifact_path=str(out_dir / "bootstrap_failure.json"))

    if not admin_404:
        content = (html_excerpt or "") + (page.content()[:8192] if hasattr(page, "content") else "")
        has_any, found, missing = _page_has_products(content, TARGET_PRODUCTS)
        if has_any:
            origin = final_url.split("/admin")[0] if "/admin" in final_url else "https://app.kajabi.com"
            return BootstrapResult(ok=True, admin_404=False, products_found=found, products_missing=missing,
                                  site_origin=origin, attempts=attempts)
        _write_bootstrap_artifact(out_dir, attempts, KAJABI_PRODUCTS_PAGE_NO_MATCH,
                                 "Products page loaded but target products not found. Check site.")
        return BootstrapResult(ok=False, error_class=KAJABI_PRODUCTS_PAGE_NO_MATCH,
                              recommended_next_action="Products page loaded but target products not found.",
                              admin_404=False, products_found=found, products_missing=missing,
                              attempts=attempts, artifact_path=str(out_dir / "bootstrap_failure.json"))

    # --- Attempt 4: KAJABI_SITES again, select Soma, retry KAJABI_PRODUCTS ---
    _try_navigate(page, KAJABI_SITES)
    _try_click_soma_site(page)

    final_url, title, admin_404, login_detected = _try_navigate(page, KAJABI_PRODUCTS)
    screenshot_path, html_excerpt = _screenshot_and_html("attempt4_sites_then_products")
    _capture_attempt(KAJABI_PRODUCTS, final_url, title, admin_404, login_detected, screenshot_path, html_excerpt)

    if login_detected:
        _write_bootstrap_artifact(out_dir, attempts, KAJABI_SESSION_EXPIRED,
                                 "Complete Kajabi login + 2FA once on aiops-1 capture flow.")
        return BootstrapResult(ok=False, error_class=KAJABI_SESSION_EXPIRED,
                              recommended_next_action="Complete Kajabi login + 2FA once on aiops-1 capture flow.",
                              login_detected=True, attempts=attempts,
                              artifact_path=str(out_dir / "bootstrap_failure.json"))

    if not admin_404:
        content = (html_excerpt or "") + (page.content()[:8192] if hasattr(page, "content") else "")
        has_any, found, missing = _page_has_products(content, TARGET_PRODUCTS)
        if has_any:
            origin = final_url.split("/admin")[0] if "/admin" in final_url else "https://app.kajabi.com"
            return BootstrapResult(ok=True, admin_404=False, products_found=found, products_missing=missing,
                                  site_origin=origin, attempts=attempts)
        _write_bootstrap_artifact(out_dir, attempts, KAJABI_PRODUCTS_PAGE_NO_MATCH,
                                 "Products page loaded but target products not found. Check site.")
        return BootstrapResult(ok=False, error_class=KAJABI_PRODUCTS_PAGE_NO_MATCH,
                              recommended_next_action="Products page loaded but target products not found.",
                              admin_404=False, products_found=found, products_missing=missing,
                              attempts=attempts, artifact_path=str(out_dir / "bootstrap_failure.json"))

    # All attempts failed with 404
    _write_bootstrap_artifact(
        out_dir, attempts, KAJABI_ADMIN_404_AFTER_BOOTSTRAP,
        "All bootstrap attempts returned 404. Platform/site issue.",
    )
    return BootstrapResult(
        ok=False,
        error_class=KAJABI_ADMIN_404_AFTER_BOOTSTRAP,
        recommended_next_action="All bootstrap attempts returned 404. Platform/site issue.",
        admin_404=True,
        attempts=attempts,
        artifact_path=str(out_dir / "bootstrap_failure.json"),
    )
