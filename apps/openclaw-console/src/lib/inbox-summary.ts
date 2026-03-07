import { join } from "path";
import { readAutonomyMode } from "./autonomy-mode";
import { listApprovals } from "./approvals";
import { loadProjectRegistrySafe } from "./projects";
import { getLastRunForProject } from "./run-recorder";
import { getPlaybooksForProject, getPluginsForProject, PluginInboxCardHint, PluginWidget } from "./plugins";
import { decide, StatePackShape } from "./policy";
import { riskLabel } from "./playbooks";
import { decideRunNext } from "./run-next";
import { getArtifactsRoot, listChildDirectories, readJsonFile, toArtifactRelativePath, toArtifactUrl } from "./server-artifacts";
import { resolveSomaLastRun } from "./soma-last-run-resolver";

export interface InboxCard {
  id: string;
  type: "HUMAN_ONLY" | "CORE_DEGRADED" | "APPROVAL_REQUIRED" | "PLUGIN_HINT";
  title: string;
  summary: string;
  project_id: string;
  approval_id?: string | null;
  proof_links: Array<{ label: string; href: string }>;
  action_label?: string;
  action_href?: string | null;
  tone: "info" | "warn" | "danger";
}

export interface ProjectInboxSummary {
  project_id: string;
  name: string;
  description: string;
  autonomy_mode: "ON" | "OFF";
  core_status: "PASS" | "FAIL" | "UNKNOWN";
  optional_status: "PASS" | "WARN" | "UNKNOWN";
  needs_human: boolean;
  approvals_pending: number;
  last_run: {
    run_id: string | null;
    action: string | null;
    status: string | null;
    finished_at: string | null;
    artifact_dir?: string | null;
  };
  proof_links: Array<{ label: string; href: string }>;
  cards: InboxCard[];
  widgets: PluginWidget[];
  playbooks: Array<{
    id: string;
    title: string;
    description: string;
    risk_level: string;
    risk_label: string;
    tags: string[];
    policy_preview: string;
    primary_action: string;
  }>;
  recommended_playbook: {
    id: string;
    title: string;
    rationale: string;
    expected_outputs: string[];
  } | null;
  business_dod_pass?: boolean | null;
  human_gate?: {
    run_id: string | null;
    novnc_url: string | null;
    browser_url: string | null;
    instruction: string | null;
  };
}

export interface InboxSummaryResponse {
  ok: true;
  autonomy_mode: {
    mode: "ON" | "OFF";
    updated_at: string | null;
    updated_by: string | null;
  };
  canary_core_status: "PASS" | "FAIL" | "UNKNOWN";
  canary_optional_status: "PASS" | "WARN" | "UNKNOWN";
  projects: ProjectInboxSummary[];
}

function loadLatestCanary(): {
  core_status: "PASS" | "FAIL" | "UNKNOWN";
  optional_status: "PASS" | "WARN" | "UNKNOWN";
  failed_checks: string[];
  proof_href: string | null;
} {
  const canaryRoot = join(getArtifactsRoot(), "system", "canary");
  const dirs = listChildDirectories(canaryRoot);
  if (dirs.length === 0) {
    return { core_status: "UNKNOWN", optional_status: "UNKNOWN", failed_checks: [], proof_href: null };
  }
  const latest = dirs[0];
  const result = readJsonFile<Record<string, unknown>>(join(canaryRoot, latest, "result.json"));
  if (!result) {
    return { core_status: "UNKNOWN", optional_status: "UNKNOWN", failed_checks: [], proof_href: null };
  }
  const coreStatus = String(result.core_status || "").toUpperCase();
  const optionalStatus = String(result.optional_status || "").toUpperCase();
  const failedInvariant = String(result.failed_invariant || "").trim();
  const failedChecks = Array.isArray(result.core_failed_checks)
    ? result.core_failed_checks.map((value) => String(value || "")).filter(Boolean)
    : failedInvariant
      ? failedInvariant.split(",").map((value) => value.trim()).filter(Boolean)
      : [];
  const inferredCore = coreStatus === "FAIL" || String(result.status || "").toUpperCase() === "DEGRADED"
    ? "FAIL"
    : coreStatus === "PASS"
      ? "PASS"
      : "UNKNOWN";
  const inferredOptional = optionalStatus === "WARN" ? "WARN" : optionalStatus === "PASS" ? "PASS" : "UNKNOWN";
  return {
    core_status: inferredCore,
    optional_status: inferredOptional,
    failed_checks: failedChecks,
    proof_href: toArtifactUrl(toArtifactRelativePath(String(result.proof || ""))),
  };
}

function loadBusinessDodStatus(): { pass: boolean | null; href: string | null } {
  const root = join(getArtifactsRoot(), "soma_kajabi", "business_dod");
  const dirs = listChildDirectories(root);
  for (const dir of dirs.slice(0, 10)) {
    const checks = readJsonFile<{ pass?: boolean }>(join(root, dir, "business_dod_checks.json"));
    if (!checks) continue;
    return {
      pass: typeof checks.pass === "boolean" ? checks.pass : null,
      href: toArtifactUrl(`artifacts/soma_kajabi/business_dod/${dir}`),
    };
  }
  return { pass: null, href: null };
}

function makeProofLinks(projectId: string, canaryHref: string | null): Array<{ label: string; href: string }> {
  const links: Array<{ label: string; href: string }> = [];
  const lastRun = getLastRunForProject(projectId);
  if (lastRun?.artifact_dir) {
    const href = toArtifactUrl(lastRun.artifact_dir);
    if (href) links.push({ label: "Last run proof", href });
  }
  if (canaryHref) {
    links.push({ label: "Canary proof", href: canaryHref });
  }
  return links;
}

function pluginHintsToCards(
  projectId: string,
  hints: PluginInboxCardHint[],
  proofLinks: Array<{ label: string; href: string }>
): InboxCard[] {
  return hints.map((hint) => ({
    id: hint.id,
    type: "PLUGIN_HINT",
    title: hint.title,
    summary: hint.summary,
    project_id: projectId,
    proof_links: proofLinks,
    tone: hint.tone,
  }));
}

export function buildInboxSummary(projectFilter?: string): InboxSummaryResponse {
  const registry = loadProjectRegistrySafe();
  const autonomy = readAutonomyMode();
  const canary = loadLatestCanary();
  const approvals = listApprovals({ status: "PENDING", limit: 200 });
  const projects = (registry?.projects ?? []).filter((project) => !projectFilter || project.id === projectFilter);

  const summaries: ProjectInboxSummary[] = projects.map((project) => {
    const projectApprovals = approvals.filter((approval) => approval.project_id === project.id);
    const lastRun = getLastRunForProject(project.id);
    const somaState = project.id === "soma_kajabi" ? resolveSomaLastRun() : null;
    const businessDod = project.id === "soma_kajabi" ? loadBusinessDodStatus() : { pass: null, href: null };
    const needsHuman = somaState?.status === "WAITING_FOR_HUMAN";
    const proofLinks = makeProofLinks(project.id, canary.proof_href);
    if (project.id === "soma_kajabi" && somaState?.artifact_dir) {
      const href = toArtifactUrl(somaState.artifact_dir);
      if (href) {
        proofLinks.unshift({ label: "Current Soma proof", href });
      }
    }
    if (project.id === "soma_kajabi" && businessDod.href) {
      proofLinks.push({ label: "Business DoD proof", href: businessDod.href });
    }

    const statePack: StatePackShape = {
      project_id: project.id,
      approvals_pending: projectApprovals.length,
      needs_human: needsHuman,
      core_status: canary.core_status,
      optional_status: canary.optional_status,
      business_dod_pass: businessDod.pass,
    };
    const playbooks = getPlaybooksForProject(project.id);
    const playbookSummaries = playbooks.map((playbook) => ({
      id: playbook.id,
      title: playbook.title,
      description: playbook.description,
      risk_level: playbook.risk_level,
      risk_label: riskLabel(playbook.risk_level),
      tags: playbook.tags,
      policy_preview: decide(playbook, statePack, autonomy.mode, "admin", { source: "manual", is_privileged: true }).decision,
      primary_action: playbook.primary_action,
    }));
    const recommendation = decideRunNext({
      project_id: project.id,
      approvals_pending: projectApprovals.length,
      needs_human: needsHuman,
      core_status: canary.core_status,
      business_dod_pass: businessDod.pass,
      playbooks,
    });
    const pluginCards = getPluginsForProject(project.id).flatMap((plugin) =>
      plugin.inboxCardsFn ? plugin.inboxCardsFn({ ...statePack, autonomy_mode: autonomy.mode }) : []
    );
    const widgets = getPluginsForProject(project.id).flatMap((plugin) =>
      plugin.widgetsFn ? plugin.widgetsFn({ ...statePack, autonomy_mode: autonomy.mode }) : []
    );
    const cards: InboxCard[] = [];

    if (needsHuman && somaState) {
      cards.push({
        id: `${project.id}-human-only`,
        type: "HUMAN_ONLY",
        title: "Human gate open",
        summary: somaState.error_class ?? "WAITING_FOR_HUMAN",
        project_id: project.id,
        proof_links: [
          ...(somaState.browser_gateway_url ? [{ label: "Open browser", href: somaState.browser_gateway_url }] : []),
          ...(somaState.novnc_url ? [{ label: "Open noVNC", href: somaState.novnc_url }] : []),
          ...proofLinks,
        ],
        action_label: "Open Browser",
        action_href: somaState.browser_gateway_url ?? somaState.novnc_url ?? null,
        tone: "warn",
      });
    }

    if (canary.core_status === "FAIL") {
      cards.push({
        id: `${project.id}-core-degraded`,
        type: "CORE_DEGRADED",
        title: "Core degraded",
        summary: canary.failed_checks.length > 0 ? canary.failed_checks.join(", ") : "Canary core checks failing.",
        project_id: project.id,
        proof_links: proofLinks,
        action_label: "Open proof",
        action_href: canary.proof_href,
        tone: "danger",
      });
    }

    for (const approval of projectApprovals) {
      const approvalLinks = [
        ...(approval.request_url ? [{ label: "Approval request", href: approval.request_url }] : []),
        ...(approval.proof_bundle_url ? [{ label: "Proof bundle", href: approval.proof_bundle_url }] : []),
      ];
      cards.push({
        id: approval.id,
        type: "APPROVAL_REQUIRED",
        title: approval.playbook_title,
        summary: approval.rationale,
        project_id: project.id,
        approval_id: approval.id,
        proof_links: approvalLinks,
        tone: "warn",
      });
    }

    cards.push(...pluginHintsToCards(project.id, pluginCards, proofLinks));

    return {
      project_id: project.id,
      name: project.name,
      description: project.description,
      autonomy_mode: autonomy.mode,
      core_status: canary.core_status,
      optional_status: canary.optional_status,
      needs_human: needsHuman,
      approvals_pending: projectApprovals.length,
      last_run: {
        run_id: lastRun?.run_id ?? somaState?.run_id ?? null,
        action: lastRun?.action ?? null,
        status: lastRun?.status ?? somaState?.status ?? null,
        finished_at: lastRun?.finished_at ?? somaState?.finished_at ?? null,
        artifact_dir: lastRun?.artifact_dir ?? somaState?.artifact_dir ?? null,
      },
      proof_links: proofLinks,
      cards,
      widgets,
      playbooks: playbookSummaries,
      recommended_playbook: recommendation
        ? {
            id: recommendation.playbook_id,
            title: recommendation.title,
            rationale: recommendation.rationale,
            expected_outputs: recommendation.expected_outputs,
          }
        : null,
      business_dod_pass: businessDod.pass,
      human_gate: project.id === "soma_kajabi" && somaState
        ? {
            run_id: somaState.run_id,
            novnc_url: somaState.novnc_url,
            browser_url: somaState.browser_gateway_url,
            instruction: somaState.instruction_line,
          }
        : undefined,
    };
  });

  return {
    ok: true,
    autonomy_mode: {
      mode: autonomy.mode,
      updated_at: autonomy.updated_at,
      updated_by: autonomy.updated_by,
    },
    canary_core_status: canary.core_status,
    canary_optional_status: canary.optional_status,
    projects: summaries,
  };
}
