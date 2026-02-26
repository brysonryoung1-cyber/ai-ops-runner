"""Unit test: novnc_framebuffer_guard.sh exists and contains xwd + not-all-black logic."""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GUARD = REPO_ROOT / "ops" / "guards" / "novnc_framebuffer_guard.sh"


def test_framebuffer_guard_exists() -> None:
    """Assert novnc_framebuffer_guard.sh exists."""
    assert GUARD.exists(), "novnc_framebuffer_guard.sh must exist"


def test_framebuffer_guard_uses_xwd() -> None:
    """Assert script uses xwd for framebuffer capture."""
    content = GUARD.read_text()
    assert "xwd" in content or "XWD_FILE" in content, "Guard must use xwd for framebuffer capture"


def test_framebuffer_guard_has_not_all_black_check() -> None:
    """Assert script has not-all-black logic (mean, variance, convert, or pixel check)."""
    content = GUARD.read_text()
    has_check = any(
        kw in content
        for kw in ["mean", "variance", "convert", "all_black", "unique", "nonzero", "is_black"]
    )
    assert has_check, "Guard must have not-all-black check (mean/variance/convert/pixel)"


def test_framebuffer_guard_has_heal_logic() -> None:
    """Assert script has heal/hard-reset logic."""
    content = GUARD.read_text()
    assert "restart" in content or "_hard_reset" in content or "pkill" in content, (
        "Guard must have heal/hard-reset logic"
    )


def test_framebuffer_guard_has_warmup_loop() -> None:
    """Assert guard has warm-up loop for all-black (kajabi_ui_ensure before final fail)."""
    content = GUARD.read_text()
    assert "FB_WARMUP_MAX" in content or "KAJABI_ENSURE" in content or "kajabi_ui_ensure" in content, (
        "Guard must have warm-up loop with kajabi_ui_ensure before treating all-black as fatal"
    )
