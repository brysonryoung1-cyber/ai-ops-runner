"""Tests for openclaw_targets.py — target validation and Tailscale IP checks.

Tests cover:
  - Tailscale CGNAT IP validation (100.64.0.0/10)
  - Target schema validation
  - Boundary conditions
"""

import json
import os
import sys
import tempfile

# Add ops/ to path for import
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from openclaw_targets import is_tailscale_ip, validate_target


# ─── is_tailscale_ip ──────────────────────────────────────────────────────────


def test_tailscale_ip_valid_range():
    """IPs in 100.64.0.0/10 should pass."""
    assert is_tailscale_ip("100.64.0.1")
    assert is_tailscale_ip("100.100.50.25")
    assert is_tailscale_ip("100.123.61.57")  # aiops-1
    assert is_tailscale_ip("100.127.255.254")


def test_tailscale_ip_boundary_start():
    """First IP in Tailscale range."""
    assert is_tailscale_ip("100.64.0.0")


def test_tailscale_ip_boundary_end():
    """Last IP in Tailscale range."""
    assert is_tailscale_ip("100.127.255.255")


def test_tailscale_ip_just_below():
    """100.63.x.x is below the Tailscale range."""
    assert not is_tailscale_ip("100.63.255.255")
    assert not is_tailscale_ip("100.63.0.1")


def test_tailscale_ip_just_above():
    """100.128.x.x is above the Tailscale range."""
    assert not is_tailscale_ip("100.128.0.0")
    assert not is_tailscale_ip("100.128.0.1")


def test_tailscale_ip_private_ranges():
    """Common private ranges should fail."""
    assert not is_tailscale_ip("192.168.1.1")
    assert not is_tailscale_ip("10.0.0.1")
    assert not is_tailscale_ip("172.16.0.1")


def test_tailscale_ip_special():
    """Special IPs should fail."""
    assert not is_tailscale_ip("0.0.0.0")
    assert not is_tailscale_ip("127.0.0.1")
    assert not is_tailscale_ip("255.255.255.255")


def test_tailscale_ip_malformed():
    """Malformed inputs should fail."""
    assert not is_tailscale_ip("")
    assert not is_tailscale_ip("not-an-ip")
    assert not is_tailscale_ip("100.64.0")  # Only 3 octets
    assert not is_tailscale_ip("100.64.0.0.0")  # 5 octets
    assert not is_tailscale_ip("100.64.0.abc")


def test_tailscale_ip_out_of_range_octets():
    """Octets > 255 or < 0 should fail."""
    assert not is_tailscale_ip("100.64.0.256")
    assert not is_tailscale_ip("100.64.0.-1")


# ─── validate_target ──────────────────────────────────────────────────────────


def test_validate_target_valid():
    """Complete valid target should pass."""
    target = {
        "name": "aiops-1",
        "host": "100.123.61.57",
        "user": "root",
        "repo_path": "/opt/ai-ops-runner",
    }
    assert validate_target(target) is None


def test_validate_target_valid_runner():
    """Runner user should be valid."""
    target = {"name": "test", "host": "100.64.0.1", "user": "runner"}
    assert validate_target(target) is None


def test_validate_target_missing_name():
    """Target without name should fail."""
    target = {"host": "100.64.0.1", "user": "root"}
    err = validate_target(target)
    assert err is not None
    assert "name" in err.lower()


def test_validate_target_missing_host():
    """Target without host should fail."""
    target = {"name": "test", "user": "root"}
    err = validate_target(target)
    assert err is not None
    assert "host" in err.lower()


def test_validate_target_non_tailscale_host():
    """Target with non-tailnet host should fail."""
    target = {"name": "test", "host": "192.168.1.1", "user": "root"}
    err = validate_target(target)
    assert err is not None
    assert "100.64" in err or "Tailscale" in err


def test_validate_target_invalid_user():
    """Target with user other than root/runner should fail."""
    target = {"name": "test", "host": "100.64.0.1", "user": "admin"}
    err = validate_target(target)
    assert err is not None
    assert "user" in err.lower()


def test_validate_target_empty_user():
    """Target with empty user should pass (defaults to root in practice)."""
    target = {"name": "test", "host": "100.64.0.1", "user": ""}
    assert validate_target(target) is None


def test_validate_target_no_user():
    """Target without user field should pass (defaults to root)."""
    target = {"name": "test", "host": "100.64.0.1"}
    assert validate_target(target) is None


def test_validate_target_public_ip():
    """Target with public IP should fail."""
    target = {"name": "test", "host": "8.8.8.8", "user": "root"}
    err = validate_target(target)
    assert err is not None


def test_validate_target_localhost():
    """Target with localhost should fail (not in Tailscale range)."""
    target = {"name": "test", "host": "127.0.0.1", "user": "root"}
    err = validate_target(target)
    assert err is not None
