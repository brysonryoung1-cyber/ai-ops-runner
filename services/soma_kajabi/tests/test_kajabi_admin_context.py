"""Tests for ensure_kajabi_soma_admin_context bootstrap helper."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from services.soma_kajabi.kajabi_admin_context import (
    KAJABI_ADMIN_404_AFTER_BOOTSTRAP,
    KAJABI_CLOUDFLARE_BLOCKED,
    KAJABI_PRODUCTS_PAGE_NO_MATCH,
    KAJABI_SESSION_EXPIRED,
    KAJABI_ADMIN,
    KAJABI_PRODUCTS,
    KAJABI_SITES,
    SOMA_ADMIN,
    SOMA_PRODUCTS,
    SOMA_SITE,
    _is_404_page,
    _is_login_page,
    _page_has_products,
)


def test_url_chain_ordering():
    """URL chain must try Soma site first, then app.kajabi.com."""
    assert SOMA_SITE == "https://zane-mccourtney.mykajabi.com"
    assert SOMA_PRODUCTS == "https://zane-mccourtney.mykajabi.com/admin/products"
    assert SOMA_ADMIN == "https://zane-mccourtney.mykajabi.com/admin"
    assert KAJABI_ADMIN == "https://app.kajabi.com/admin"
    assert KAJABI_PRODUCTS == "https://app.kajabi.com/admin/products"
    assert KAJABI_SITES == "https://app.kajabi.com/admin/sites"


def test_classification_login_vs_404():
    """Classification: login page vs 404 page."""
    # Login detection
    assert _is_login_page("https://app.kajabi.com/login", "") is True
    assert _is_login_page("https://x.com/sign_in", "") is True
    assert _is_login_page("https://x.com/admin", "Please sign in to continue") is True
    assert _is_login_page("https://x.com/admin", "Log in to your account") is True
    assert _is_login_page("https://x.com/admin/products", "Products list") is False

    # 404 detection
    assert _is_404_page("404 - Not Found", "") is True
    assert _is_404_page("Page doesn't exist", "") is True
    assert _is_404_page("Something went wrong", "404 error") is True
    assert _is_404_page("Kajabi Admin - Products", "Home User Library") is False


def test_page_has_products():
    """Product presence check."""
    content = "Home User Library and Practitioner Library are here"
    has_any, found, missing = _page_has_products(content, ["Home User Library", "Practitioner Library"])
    assert has_any is True
    assert "Home User Library" in found
    assert "Practitioner Library" in found
    assert missing == []

    content = "Only Home User Library"
    has_any, found, missing = _page_has_products(content, ["Home User Library", "Practitioner Library"])
    assert has_any is True
    assert "Home User Library" in found
    assert "Practitioner Library" in missing

    content = "No products here"
    has_any, found, missing = _page_has_products(content, ["Home User Library", "Practitioner Library"])
    assert has_any is False
    assert found == []
    assert missing == ["Home User Library", "Practitioner Library"]


def test_error_classes_defined():
    """Error classes must be distinct."""
    assert KAJABI_SESSION_EXPIRED == "KAJABI_SESSION_EXPIRED"
    assert KAJABI_CLOUDFLARE_BLOCKED == "KAJABI_CLOUDFLARE_BLOCKED"
    assert KAJABI_ADMIN_404_AFTER_BOOTSTRAP == "KAJABI_ADMIN_404_AFTER_BOOTSTRAP"
    assert KAJABI_PRODUCTS_PAGE_NO_MATCH == "KAJABI_PRODUCTS_PAGE_NO_MATCH"


def test_cloudflare_not_login():
    """Cloudflare block must not be classified as login."""
    from services.soma_kajabi.kajabi_admin_context import _is_cloudflare_blocked, _is_login_page

    cf_content = "<html><title>Attention Required! | Cloudflare</title><body>Sorry, you have been blocked</body></html>"
    assert _is_cloudflare_blocked(cf_content) is True
    assert _is_cloudflare_blocked("", title="Attention Required! | Cloudflare") is True
    assert _is_cloudflare_blocked("Sorry, you have been blocked", title="Cloudflare") is True
    assert _is_cloudflare_blocked("normal page", title="Kajabi Admin") is False
    assert _is_login_page("https://app.kajabi.com/login", cf_content) is False
