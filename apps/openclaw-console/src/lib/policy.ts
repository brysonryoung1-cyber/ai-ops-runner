import { Playbook, PolicyDecision } from "./playbooks";

export interface StatePackShape {
  project_id: string;
  approvals_pending: number;
  needs_human: boolean;
  core_status: "PASS" | "FAIL" | "UNKNOWN";
  optional_status: "PASS" | "WARN" | "UNKNOWN";
  business_dod_pass?: boolean | null;
}

export interface PolicyContext {
  source?: "manual" | "autopilot";
  confirm_phrase?: string | null;
  is_privileged?: boolean;
}

export function decide(
  playbook: Playbook,
  statePack: StatePackShape,
  autonomyMode: "ON" | "OFF",
  userRole: string,
  context: PolicyContext = {}
): PolicyDecision {
  const source = context.source ?? "manual";
  const isPrivileged = context.is_privileged === true || userRole === "admin";
  const confirmPhrase = String(context.confirm_phrase ?? "").trim().toUpperCase();

  if (source === "autopilot" && autonomyMode === "OFF" && playbook.mutates_external) {
    return {
      decision: "SKIP_AUTONOMY_OFF",
      reason: "Autonomy mode is OFF, so mutating playbooks are skipped for automated runners.",
      allowed: false,
      guardrails: {
        autonomy_mode: autonomyMode,
        read_only_allowed: true,
      },
    };
  }

  if (playbook.policy_default === "APPROVAL") {
    return {
      decision: "APPROVAL",
      reason:
        statePack.approvals_pending > 0
          ? "This playbook is approval-gated and there are unresolved approval items in the queue."
          : "This playbook requires explicit operator approval before execution.",
      required_approval: true,
      allowed: false,
      guardrails: {
        autonomy_mode: autonomyMode,
      },
    };
  }

  if (playbook.policy_default === "BREAK_GLASS") {
    const confirmed = confirmPhrase === "RUN";
    const roleAllowed = isPrivileged;
    return {
      decision: "BREAK_GLASS",
      reason: roleAllowed && confirmed
        ? "Break-glass guardrails satisfied."
        : "Break-glass playbooks require an admin role and the confirm phrase RUN.",
      allowed: roleAllowed && confirmed,
      guardrails: {
        autonomy_mode: autonomyMode,
        requires_confirm_phrase: true,
        confirm_phrase: "RUN",
        confirmed,
        privileged_role_required: true,
        role_allowed: roleAllowed,
      },
    };
  }

  return {
    decision: "AUTO",
    reason: playbook.mutates_external
      ? "Low-friction playbook: safe to run without extra approval when autonomy allows it."
      : "Read-only playbook: safe to run immediately.",
    allowed: true,
    guardrails: {
      autonomy_mode: autonomyMode,
      read_only_allowed: !playbook.mutates_external,
    },
  };
}
