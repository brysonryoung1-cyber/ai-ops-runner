"""Tests for openclaw_console_token.py — token generation and masking.

Tests cover:
  - Token generation (length, hex, uniqueness)
  - Token masking (safe display)
"""

import os
import sys

# Add ops/ to path for import
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from openclaw_console_token import generate_token, _mask_token


# ─── Token generation ─────────────────────────────────────────────────────────


def test_generate_token_length():
    """Generated token should be 64 hex chars (32 bytes = 256 bits)."""
    token = generate_token()
    assert len(token) == 64


def test_generate_token_is_hex():
    """Generated token must be valid hexadecimal."""
    token = generate_token()
    int(token, 16)  # Should not raise ValueError


def test_generate_token_unique():
    """Two generated tokens must differ (probabilistic but essentially certain)."""
    t1 = generate_token()
    t2 = generate_token()
    assert t1 != t2


def test_generate_token_no_prefix():
    """Token should not have a prefix like 'sk-' — it's raw hex."""
    token = generate_token()
    assert not token.startswith("sk-")


# ─── Token masking ─────────────────────────────────────────────────────────────


def test_mask_token_long():
    """Long tokens show first 4 + ... + last 4."""
    token = "abcdefghijklmnopqrst"
    masked = _mask_token(token)
    assert masked == "abcd...qrst"
    # Ensure middle chars are not exposed
    assert "efghijklmnop" not in masked


def test_mask_token_short():
    """Short tokens (<=12 chars) are fully masked."""
    assert _mask_token("abc") == "****"
    assert _mask_token("123456789012") == "****"


def test_mask_token_exactly_13():
    """13-char token should show first 4 + ... + last 4."""
    masked = _mask_token("1234567890abc")
    assert masked == "1234...0abc"


def test_mask_token_real_size():
    """64-char token (actual production size) masks correctly."""
    token = generate_token()
    masked = _mask_token(token)
    assert masked.startswith(token[:4])
    assert masked.endswith(token[-4:])
    assert "..." in masked
    assert len(masked) == 11  # 4 + 3 + 4
