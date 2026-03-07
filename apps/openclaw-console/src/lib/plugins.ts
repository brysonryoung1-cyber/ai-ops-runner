import { Playbook } from "./playbooks";

export interface PluginRuntimeState {
  project_id: string;
  approvals_pending: number;
  needs_human: boolean;
  core_status: "PASS" | "FAIL" | "UNKNOWN";
  optional_status: "PASS" | "WARN" | "UNKNOWN";
  business_dod_pass?: boolean | null;
  autonomy_mode: "ON" | "OFF";
}

export interface PluginInboxCardHint {
  id: string;
  title: string;
  summary: string;
  tone: "info" | "warn" | "danger";
  tags: string[];
}

export interface PluginWidget {
  id: string;
  label: string;
  value: string;
  tone?: "neutral" | "warn" | "danger" | "success";
}

export interface PluginDefinition {
  id: string;
  projects: string[];
  playbooks: Playbook[];
  inboxCardsFn?: (state: PluginRuntimeState) => PluginInboxCardHint[];
  widgetsFn?: (state: PluginRuntimeState) => PluginWidget[];
}

const registry = new Map<string, PluginDefinition>();

export function registerPlugin(plugin: PluginDefinition): void {
  registry.set(plugin.id, plugin);
}

function definePlaybook(input: Omit<Playbook, "plugin_id"> & { plugin_id: string }): Playbook {
  return input;
}

const STATIC_PLUGINS: PluginDefinition[] = [
  {
    id: "infra_openclaw",
    projects: ["infra_openclaw"],
    playbooks: [
      definePlaybook({
        id: "infra.review_approvals",
        title: "Review Approvals",
        description: "Open the single approval queue for infrastructure work.",
        project_id: "infra_openclaw",
        plugin_id: "infra_openclaw",
        primary_action: "noop.review_approvals",
        tags: ["inbox", "review", "approval"],
        risk_level: "low",
        policy_default: "AUTO",
        mutates_external: false,
        prerequisites: [],
        proof_expectations: ["approval queue snapshot", "proof links"],
        kind: "review",
      }),
      definePlaybook({
        id: "infra.run_doctor_heal",
        title: "Run Doctor/Heal",
        description: "Run the deterministic infrastructure audit and self-heal path.",
        project_id: "infra_openclaw",
        plugin_id: "infra_openclaw",
        primary_action: "openclaw_hq_audit",
        tags: ["doctor", "heal", "infra"],
        risk_level: "med",
        policy_default: "AUTO",
        mutates_external: true,
        prerequisites: ["HQ reachable"],
        proof_expectations: ["HQ audit summary", "linked remediation artifacts"],
      }),
      definePlaybook({
        id: "infra.run_to_done",
        title: "Run to Done",
        description: "Deploy and verify the current tree with full proof and rollback guardrails.",
        project_id: "infra_openclaw",
        plugin_id: "infra_openclaw",
        primary_action: "deploy_and_verify",
        tags: ["deploy", "verify", "break-glass"],
        risk_level: "high",
        policy_default: "BREAK_GLASS",
        mutates_external: true,
        prerequisites: ["Healthy host executor", "Operator confirm phrase"],
        proof_expectations: ["deploy receipt", "post-deploy proof bundle"],
      }),
    ],
    inboxCardsFn: (state) =>
      state.core_status === "FAIL"
        ? [
            {
              id: "infra-core-degraded",
              title: "Infrastructure core degraded",
              summary: "Doctor/Heal is the fastest path back to green.",
              tone: "danger",
              tags: ["core", "doctor"],
            },
          ]
        : [],
    widgetsFn: (state) => [
      {
        id: "infra-autonomy",
        label: "Autonomy",
        value: state.autonomy_mode,
        tone: state.autonomy_mode === "ON" ? "success" : "warn",
      },
    ],
  },
  {
    id: "soma_kajabi",
    projects: ["soma_kajabi"],
    playbooks: [
      definePlaybook({
        id: "soma.review_approvals",
        title: "Review Approvals",
        description: "Review and resolve queued Soma approvals.",
        project_id: "soma_kajabi",
        plugin_id: "soma_kajabi",
        primary_action: "noop.review_approvals",
        tags: ["inbox", "review", "approval"],
        risk_level: "low",
        policy_default: "AUTO",
        mutates_external: false,
        prerequisites: [],
        proof_expectations: ["approval queue snapshot", "proof links"],
        kind: "review",
      }),
      definePlaybook({
        id: "soma.reauth_resume",
        title: "Reauth & Resume",
        description: "Open the human gate, capture auth, and resume the Soma lane.",
        project_id: "soma_kajabi",
        plugin_id: "soma_kajabi",
        primary_action: "soma_kajabi_reauth_and_resume",
        tags: ["human-only", "reauth", "resume"],
        risk_level: "med",
        policy_default: "AUTO",
        mutates_external: true,
        prerequisites: ["Active HUMAN_ONLY gate"],
        proof_expectations: ["WAITING_FOR_HUMAN artifact", "resume run artifact"],
      }),
      definePlaybook({
        id: "soma.run_doctor_heal",
        title: "Run Doctor/Heal",
        description: "Run the noVNC and lane recovery sequence, then resume if safe.",
        project_id: "soma_kajabi",
        plugin_id: "soma_kajabi",
        primary_action: "soma_novnc_oneclick_recovery",
        tags: ["doctor", "heal", "novnc", "recovery"],
        risk_level: "med",
        policy_default: "AUTO",
        mutates_external: true,
        prerequisites: ["Soma lane enabled"],
        proof_expectations: ["noVNC recovery artifact", "resume proof or READY_FOR_HUMAN output"],
      }),
      definePlaybook({
        id: "soma.fix_business_dod",
        title: "Fix Business DoD",
        description: "Run the targeted Kajabi Business DoD fixer and then re-verify.",
        project_id: "soma_kajabi",
        plugin_id: "soma_kajabi",
        primary_action: "soma_business_dod_fixer",
        tags: ["business-dod", "kajabi", "approval"],
        risk_level: "high",
        policy_default: "APPROVAL",
        mutates_external: true,
        prerequisites: ["Latest business DoD failed"],
        proof_expectations: ["before/after business DoD checks", "UI fix artifact"],
      }),
      definePlaybook({
        id: "soma.run_to_done",
        title: "Run to Done",
        description: "Trigger the end-to-end Soma lane and follow it to a terminal result.",
        project_id: "soma_kajabi",
        plugin_id: "soma_kajabi",
        primary_action: "soma_run_to_done",
        tags: ["run-next", "autopilot", "soma"],
        risk_level: "med",
        policy_default: "AUTO",
        mutates_external: true,
        prerequisites: ["Kajabi auth available or HUMAN_ONLY handled"],
        proof_expectations: ["run_to_done proof", "acceptance artifact links"],
      }),
    ],
    inboxCardsFn: (state) => {
      if (state.business_dod_pass === false) {
        return [
          {
            id: "soma-business-dod",
            title: "Business DoD failing",
            summary: "Soma can finish technically while still failing business acceptance.",
            tone: "warn",
            tags: ["business-dod", "acceptance"],
          },
        ];
      }
      return [];
    },
    widgetsFn: (state) => [
      {
        id: "soma-human-only",
        label: "Human gate",
        value: state.needs_human ? "Open" : "Clear",
        tone: state.needs_human ? "warn" : "success",
      },
    ],
  },
  {
    id: "pred_markets",
    projects: ["pred_markets"],
    playbooks: [
      definePlaybook({
        id: "pred.review_approvals",
        title: "Review Approvals",
        description: "Review and resolve queued Prediction Markets approvals.",
        project_id: "pred_markets",
        plugin_id: "pred_markets",
        primary_action: "noop.review_approvals",
        tags: ["inbox", "review", "approval"],
        risk_level: "low",
        policy_default: "AUTO",
        mutates_external: false,
        prerequisites: [],
        proof_expectations: ["approval queue snapshot", "proof links"],
        kind: "review",
      }),
      definePlaybook({
        id: "pred.run_health",
        title: "Run Health Report",
        description: "Run the deterministic market mirror health report.",
        project_id: "pred_markets",
        plugin_id: "pred_markets",
        primary_action: "pred_markets.report.health",
        tags: ["health", "report", "readonly"],
        risk_level: "low",
        policy_default: "AUTO",
        mutates_external: false,
        prerequisites: [],
        proof_expectations: ["health artifact", "report output"],
      }),
      definePlaybook({
        id: "pred.run_to_done",
        title: "Run to Done",
        description: "Run the latest market mirror capture into canonical artifacts.",
        project_id: "pred_markets",
        plugin_id: "pred_markets",
        primary_action: "pred_markets.mirror.run",
        tags: ["mirror", "phase0", "approval"],
        risk_level: "med",
        policy_default: "APPROVAL",
        mutates_external: false,
        prerequisites: ["Operator approval"],
        proof_expectations: ["mirror artifact bundle", "canonical snapshot links"],
      }),
    ],
  },
  {
    id: "orb_monitor",
    projects: ["orb_backtest"],
    playbooks: [
      definePlaybook({
        id: "orb.review_approvals",
        title: "Review Approvals",
        description: "Review and resolve queued ORB approvals.",
        project_id: "orb_backtest",
        plugin_id: "orb_monitor",
        primary_action: "noop.review_approvals",
        tags: ["inbox", "review", "approval"],
        risk_level: "low",
        policy_default: "AUTO",
        mutates_external: false,
        prerequisites: [],
        proof_expectations: ["approval queue snapshot"],
        kind: "review",
      }),
      definePlaybook({
        id: "orb.run_to_done",
        title: "Run to Done",
        description: "Run the Tier-1 ORB backtest lane with approval and proof.",
        project_id: "orb_backtest",
        plugin_id: "orb_monitor",
        primary_action: "orb.backtest.bulk",
        tags: ["orb", "backtest", "approval"],
        risk_level: "med",
        policy_default: "APPROVAL",
        mutates_external: false,
        prerequisites: ["Soma Phase 0 baseline pass"],
        proof_expectations: ["backtest artifact bundle"],
      }),
    ],
  },
];

for (const plugin of STATIC_PLUGINS) {
  registerPlugin(plugin);
}

export function getPlugins(): PluginDefinition[] {
  return Array.from(registry.values());
}

export function getPluginsForProject(projectId: string): PluginDefinition[] {
  return getPlugins().filter((plugin) => plugin.projects.includes(projectId));
}

export function getPlaybooksForProject(projectId: string): Playbook[] {
  return getPluginsForProject(projectId).flatMap((plugin) => plugin.playbooks);
}

export function getAllPlaybooks(): Playbook[] {
  return getPlugins().flatMap((plugin) => plugin.playbooks);
}

export function getPlaybookById(playbookId: string): Playbook | null {
  return getAllPlaybooks().find((playbook) => playbook.id === playbookId) ?? null;
}
