"""Safe Playwright page accessors — avoid crashes when page/context/browser is closed.

Use in error handlers, finally blocks, and polling loops. Replaces direct
page.title(), page.screenshot(), page.content() calls that can raise
'Target page, context or browser has been closed'.
"""

from __future__ import annotations

from typing import Any


def _is_closed(page: Any) -> bool:
    """Return True if page is None or closed."""
    if page is None:
        return True
    try:
        return getattr(page, "is_closed", lambda: False)()
    except Exception:
        return True


def safe_url(page: Any) -> str:
    """Return page URL or '<closed>' if page is None or closed."""
    if _is_closed(page):
        return "<closed>"
    try:
        return page.url or ""
    except Exception:
        return "<closed>"


def safe_title(page: Any) -> str:
    """Return page title or '<closed>' if page is None or closed."""
    if _is_closed(page):
        return "<closed>"
    try:
        return page.title() or ""
    except Exception:
        return "<closed>"


def safe_content_excerpt(page: Any, max_len: int = 8192) -> str:
    """Return page content excerpt or empty string if page is None or closed."""
    if _is_closed(page):
        return ""
    try:
        content = getattr(page, "content", None)
        if content is None:
            return ""
        return content()[:max_len] if callable(content) else str(content)[:max_len]
    except Exception:
        return ""


def safe_screenshot(page: Any, path: str) -> bool:
    """Take screenshot; no-op if page is None or closed. Returns True if succeeded."""
    if _is_closed(page):
        return False
    try:
        page.screenshot(path=path)
        return True
    except Exception:
        return False


def is_browser_closed_error(exc: BaseException) -> bool:
    """Return True if exception indicates page/context/browser was closed."""
    msg = str(exc).lower()
    return (
        "target page, context or browser has been closed" in msg
        or "targetclosederror" in msg
        or ("browsertype.launch" in msg and "closed" in msg)
    )
