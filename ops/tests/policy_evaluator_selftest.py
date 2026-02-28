#!/usr/bin/env python3
"""
policy_evaluator_selftest — Tests for the policy evaluator library.

Tests:
  1. Policy denies destructive ops by default (no approval)
  2. Policy allows readonly ops
  3. Policy denies unknown actions (fail-closed)
  4. rootd validates allowlisted commands
  5. rootd rejects non-allowlisted commands
  6. Policy denies non-allowlisted units for systemctl_restart
"""
import json
import os
import sys
import tempfile

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, "..", ".."))
sys.path.insert(0, ROOT_DIR)

from ops.policy.policy_evaluator import PolicyEvaluator


def test_denies_destructive_without_approval():
    ev = PolicyEvaluator()
    result = ev.evaluate("rollback", operator_approved=False)
    assert not result.allowed, f"Expected DENY for destructive_ops without approval, got ALLOW"
    assert result.requires_human_approval, "Expected requires_human_approval=True"
    assert "approval" in result.reason.lower(), f"Expected approval-related reason, got: {result.reason}"
    print("  PASS: policy denies destructive ops without approval")


def test_allows_destructive_with_approval():
    ev = PolicyEvaluator()
    result = ev.evaluate("rollback", operator_approved=True)
    assert result.allowed, f"Expected ALLOW for destructive_ops with approval, got DENY: {result.reason}"
    print("  PASS: policy allows destructive ops with approval")


def test_allows_readonly():
    ev = PolicyEvaluator()
    result = ev.evaluate("doctor")
    assert result.allowed, f"Expected ALLOW for readonly action, got DENY: {result.reason}"
    assert result.tier == "readonly"
    assert not result.requires_rootd
    print("  PASS: policy allows readonly ops")


def test_denies_unknown_action():
    ev = PolicyEvaluator()
    result = ev.evaluate("nonexistent_action_xyz")
    assert not result.allowed, "Expected DENY for unknown action (fail-closed)"
    assert "not in the policy registry" in result.reason.lower() or "denied" in result.reason.lower()
    print("  PASS: policy denies unknown actions (fail-closed)")


def test_rootd_allows_valid_restart():
    ev = PolicyEvaluator()
    result = ev.validate_rootd_command("systemctl_restart", {"unit": "openclaw-hostd.service"})
    assert result.allowed, f"Expected ALLOW for allowlisted unit restart, got: {result.reason}"
    print("  PASS: rootd allows allowlisted systemctl_restart")


def test_rootd_denies_non_allowlisted_command():
    ev = PolicyEvaluator()
    result = ev.validate_rootd_command("rm_rf_everything", {})
    assert not result.allowed, "Expected DENY for non-allowlisted rootd command"
    assert "not in the allowlist" in result.reason.lower()
    print("  PASS: rootd denies non-allowlisted commands")


def test_rootd_denies_non_allowlisted_unit():
    ev = PolicyEvaluator()
    result = ev.validate_rootd_command("systemctl_restart", {"unit": "sshd.service"})
    assert not result.allowed, "Expected DENY for non-allowlisted unit"
    assert "not in systemctl_restart allowlist" in result.reason.lower()
    print("  PASS: rootd denies non-allowlisted units")


def test_rootd_denies_non_allowlisted_serve_target():
    ev = PolicyEvaluator()
    result = ev.validate_rootd_command("tailscale_serve", {"target": "http://0.0.0.0:9999"})
    assert not result.allowed, "Expected DENY for non-allowlisted serve target"
    print("  PASS: rootd denies non-allowlisted tailscale serve targets")


def test_rootd_denies_non_allowlisted_path():
    ev = PolicyEvaluator()
    result = ev.validate_rootd_command("write_etc_config", {"path": "/etc/passwd"})
    assert not result.allowed, "Expected DENY for non-allowlisted path"
    print("  PASS: rootd denies non-allowlisted write paths")


def test_privileged_ops_requires_rootd():
    ev = PolicyEvaluator()
    result = ev.evaluate("guard")
    assert result.allowed, f"Expected ALLOW for privileged_ops, got: {result.reason}"
    assert result.requires_rootd, "Expected requires_rootd=True for privileged_ops"
    print("  PASS: privileged_ops requires rootd")


def test_summary():
    ev = PolicyEvaluator()
    summary = ev.to_summary()
    assert "version" in summary
    assert "tiers" in summary
    assert len(summary["tiers"]) == 4
    print("  PASS: summary returns valid structure")


def main():
    print("=== Policy Evaluator Self-Test ===")
    tests = [
        test_denies_destructive_without_approval,
        test_allows_destructive_with_approval,
        test_allows_readonly,
        test_denies_unknown_action,
        test_rootd_allows_valid_restart,
        test_rootd_denies_non_allowlisted_command,
        test_rootd_denies_non_allowlisted_unit,
        test_rootd_denies_non_allowlisted_serve_target,
        test_rootd_denies_non_allowlisted_path,
        test_privileged_ops_requires_rootd,
        test_summary,
    ]

    passed = 0
    failed = 0
    for test_fn in tests:
        try:
            test_fn()
            passed += 1
        except Exception as e:
            print(f"  FAIL: {test_fn.__name__}: {e}")
            failed += 1

    print(f"\n=== Results: {passed} passed, {failed} failed ===")
    sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    main()
