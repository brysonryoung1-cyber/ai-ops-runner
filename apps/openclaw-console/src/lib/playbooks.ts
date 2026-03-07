export type RiskLevel = "low" | "med" | "high";
export type PolicyDefault = "AUTO" | "APPROVAL" | "BREAK_GLASS";
export type PolicyDecisionName =
  | "AUTO"
  | "APPROVAL"
  | "BREAK_GLASS"
  | "DENY"
  | "SKIP_AUTONOMY_OFF";

export interface Playbook {
  id: string;
  title: string;
  description: string;
  project_id: string;
  plugin_id: string;
  primary_action: string;
  tags: string[];
  risk_level: RiskLevel;
  policy_default: PolicyDefault;
  mutates_external: boolean;
  prerequisites: string[];
  proof_expectations: string[];
  kind?: "playbook" | "review";
}

export interface PolicyGuardrails {
  autonomy_mode: "ON" | "OFF";
  requires_confirm_phrase?: boolean;
  confirm_phrase?: string;
  confirmed?: boolean;
  privileged_role_required?: boolean;
  role_allowed?: boolean;
  read_only_allowed?: boolean;
}

export interface PolicyDecision {
  decision: PolicyDecisionName;
  reason: string;
  required_approval?: boolean;
  allowed: boolean;
  guardrails: PolicyGuardrails;
}

export interface PlaybookCatalogEntry extends Playbook {
  policy_preview: PolicyDecision;
}

export function isReviewOnlyPlaybook(playbook: Playbook): boolean {
  return playbook.kind === "review" || playbook.primary_action.startsWith("noop.");
}

export function riskLabel(level: RiskLevel): string {
  if (level === "high") return "High risk";
  if (level === "med") return "Medium risk";
  return "Low risk";
}
