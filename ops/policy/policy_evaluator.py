#!/usr/bin/env python3
"""
policy_evaluator — Deterministic (no-LLM) policy engine for OpenClaw privilege tiers.

Used by hostd, rootd, and HQ to evaluate whether an action is permitted at a given
tier. Fail-closed: unknown actions are denied. No secrets in any output.

Usage:
    from ops.policy.policy_evaluator import PolicyEvaluator
    ev = PolicyEvaluator()
    result = ev.evaluate("rootd.systemctl_restart", operator_approved=False)
    if not result.allowed:
        print(result.reason)  # single clear reason
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Optional


PERMISSIONS_PATH = os.path.join(os.path.dirname(__file__), "permissions.json")

TIER_ORDER = ["readonly", "low_risk_ops", "privileged_ops", "destructive_ops"]


@dataclass(frozen=True)
class PolicyResult:
    allowed: bool
    action: str
    tier: Optional[str]
    reason: str
    requires_rootd: bool = False
    requires_human_approval: bool = False


class PolicyEvaluator:
    """Deterministic policy evaluator. Loads permissions.json once; immutable after init."""

    def __init__(self, permissions_path: str | None = None):
        path = permissions_path or PERMISSIONS_PATH
        with open(path, "r", encoding="utf-8") as f:
            self._data = json.load(f)
        self._tiers: dict = self._data.get("tiers", {})
        self._actions: dict = self._data.get("actions", {})
        self._rootd_allowlist: dict = self._data.get("rootd_allowlist", {})

    def evaluate(self, action: str, *, operator_approved: bool = False) -> PolicyResult:
        """Evaluate whether *action* is permitted. Fail-closed on unknown action."""
        if action not in self._actions:
            return PolicyResult(
                allowed=False,
                action=action,
                tier=None,
                reason=f"Action '{action}' is not in the policy registry. Denied (fail-closed).",
            )

        action_def = self._actions[action]
        tier_name = action_def.get("tier")
        if tier_name not in self._tiers:
            return PolicyResult(
                allowed=False,
                action=action,
                tier=tier_name,
                reason=f"Tier '{tier_name}' is not defined. Denied (fail-closed).",
            )

        tier = self._tiers[tier_name]
        requires_rootd = tier.get("requires_rootd", False)
        requires_approval = tier.get("requires_human_approval", False)

        if requires_approval and not operator_approved:
            return PolicyResult(
                allowed=False,
                action=action,
                tier=tier_name,
                reason=f"Action '{action}' requires human approval (tier: {tier_name}). Denied.",
                requires_rootd=requires_rootd,
                requires_human_approval=True,
            )

        return PolicyResult(
            allowed=True,
            action=action,
            tier=tier_name,
            reason=f"Allowed at tier '{tier_name}'.",
            requires_rootd=requires_rootd,
            requires_human_approval=requires_approval,
        )

    def get_tier(self, action: str) -> str | None:
        """Return the tier name for an action, or None if unknown."""
        entry = self._actions.get(action)
        return entry.get("tier") if entry else None

    def requires_rootd(self, action: str) -> bool:
        """Return True if the action's tier requires rootd."""
        tier_name = self.get_tier(action)
        if not tier_name or tier_name not in self._tiers:
            return False
        return self._tiers[tier_name].get("requires_rootd", False)

    def requires_human_approval(self, action: str) -> bool:
        """Return True if the action's tier requires human approval."""
        tier_name = self.get_tier(action)
        if not tier_name or tier_name not in self._tiers:
            return True  # fail-closed: unknown => require approval
        return self._tiers[tier_name].get("requires_human_approval", False)

    def validate_rootd_command(self, command: str, args: dict) -> PolicyResult:
        """Validate that a rootd command + args are within the allowlist. Fail-closed."""
        if command not in self._rootd_allowlist:
            return PolicyResult(
                allowed=False,
                action=f"rootd.{command}",
                tier="privileged_ops",
                reason=f"rootd command '{command}' is not in the allowlist. Denied.",
                requires_rootd=True,
            )

        allowlist_entry = self._rootd_allowlist[command]

        if command == "systemctl_restart":
            unit = args.get("unit", "")
            allowed_units = allowlist_entry.get("allowed_units", [])
            if unit not in allowed_units:
                return PolicyResult(
                    allowed=False,
                    action=f"rootd.{command}",
                    tier="privileged_ops",
                    reason=f"Unit '{unit}' is not in systemctl_restart allowlist. Denied.",
                    requires_rootd=True,
                )

        elif command == "systemctl_enable":
            unit = args.get("unit", "")
            allowed_units = allowlist_entry.get("allowed_units", [])
            if unit not in allowed_units:
                return PolicyResult(
                    allowed=False,
                    action=f"rootd.{command}",
                    tier="privileged_ops",
                    reason=f"Unit '{unit}' is not in systemctl_enable allowlist. Denied.",
                    requires_rootd=True,
                )

        elif command == "tailscale_serve":
            target = args.get("target", "")
            allowed_targets = allowlist_entry.get("allowed_targets", [])
            if target not in allowed_targets:
                return PolicyResult(
                    allowed=False,
                    action=f"rootd.{command}",
                    tier="privileged_ops",
                    reason=f"Tailscale Serve target '{target}' is not in allowlist. Denied.",
                    requires_rootd=True,
                )

        elif command == "write_etc_config":
            path = args.get("path", "")
            allowed_paths = allowlist_entry.get("allowed_paths", [])
            if path not in allowed_paths:
                return PolicyResult(
                    allowed=False,
                    action=f"rootd.{command}",
                    tier="privileged_ops",
                    reason=f"Path '{path}' is not in write_etc_config allowlist. Denied.",
                    requires_rootd=True,
                )

        return PolicyResult(
            allowed=True,
            action=f"rootd.{command}",
            tier="privileged_ops",
            reason=f"rootd command '{command}' is allowlisted with provided args.",
            requires_rootd=True,
        )

    def list_actions_by_tier(self, tier_name: str) -> list[str]:
        """Return all action names for a given tier."""
        return [a for a, d in self._actions.items() if d.get("tier") == tier_name]

    def to_summary(self) -> dict:
        """Return a JSON-safe summary of the policy for UI/audit display."""
        return {
            "version": self._data.get("version", "unknown"),
            "tiers": {
                name: {
                    "level": t.get("level"),
                    "requires_rootd": t.get("requires_rootd", False),
                    "requires_human_approval": t.get("requires_human_approval", False),
                    "action_count": len(self.list_actions_by_tier(name)),
                }
                for name, t in self._tiers.items()
            },
        }


def main() -> None:
    """CLI: validate permissions.json and print summary."""
    ev = PolicyEvaluator()
    summary = ev.to_summary()
    print(json.dumps(summary, indent=2))
    for tier_name in TIER_ORDER:
        actions = ev.list_actions_by_tier(tier_name)
        for action in actions:
            result = ev.evaluate(action)
            status = "ALLOW" if result.allowed else "DENY"
            print(f"  [{status}] {action} -> {tier_name}")


if __name__ == "__main__":
    main()
