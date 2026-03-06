"""Hermetic tests for Kajabi Business DoD UI fixer helpers."""

from __future__ import annotations

from services.soma_kajabi.kajabi_ui_fixer import (
    KAJABI_2FA_REQUIRED,
    PROFILE_LOCK_ERROR_CLASS,
    classify_human_only_condition,
    has_raw_category_text,
    should_skip_privacy_fix,
)


def test_idempotency_helpers_skip_when_already_fixed():
    assert should_skip_privacy_fix(200) is True
    assert should_skip_privacy_fix(302) is True
    assert should_skip_privacy_fix(404) is False
    assert has_raw_category_text("RAW – Needs Review") is True
    assert has_raw_category_text("RAW - Needs Review") is True
    assert has_raw_category_text("Some Other Category") is False


def test_human_only_detection_cloudflare():
    detected = classify_human_only_condition(
        url="https://app.kajabi.com/admin/products",
        title="Attention Required! | Cloudflare",
        content="Sorry, you have been blocked",
    )
    assert detected is not None
    assert detected["error_class"] == "KAJABI_CLOUDFLARE_BLOCKED"


def test_human_only_detection_profile_lock():
    detected = classify_human_only_condition(
        launch_error="ProcessSingleton: profile appears to be in use by another browser",
    )
    assert detected is not None
    assert detected["error_class"] == PROFILE_LOCK_ERROR_CLASS


def test_human_only_detection_2fa():
    detected = classify_human_only_condition(
        url="https://app.kajabi.com/admin",
        title="Sign in",
        content="Enter your two-factor authentication verification code",
    )
    assert detected is not None
    assert detected["error_class"] == KAJABI_2FA_REQUIRED

