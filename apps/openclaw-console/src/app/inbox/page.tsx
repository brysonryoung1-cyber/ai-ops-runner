"use client";

import { startTransition, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { GlassButton, GlassCard } from "@/components/glass";
import { useToken } from "@/lib/token-context";

interface ApprovalCard {
  id: string;
  type: "HUMAN_ONLY" | "CORE_DEGRADED" | "APPROVAL_REQUIRED" | "PLUGIN_HINT";
  title: string;
  summary: string;
  project_id: string;
  project_name?: string;
  approval_id?: string | null;
  proof_links: Array<{ label: string; href: string }>;
  action_label?: string;
  action_href?: string | null;
  tone: "info" | "warn" | "danger";
}

interface ProjectSummary {
  project_id: string;
  name: string;
  description: string;
  autonomy_mode: "ON" | "OFF";
  core_status: "PASS" | "FAIL" | "UNKNOWN";
  optional_status: "PASS" | "WARN" | "UNKNOWN";
  needs_human: boolean;
  approvals_pending: number;
  proof_links: Array<{ label: string; href: string }>;
  cards: ApprovalCard[];
  recommended_playbook: {
    id: string;
    title: string;
    rationale: string;
    expected_outputs: string[];
  } | null;
  human_gate?: {
    run_id: string | null;
    novnc_url: string | null;
    browser_url: string | null;
    instruction: string | null;
  };
  last_run: {
    run_id: string | null;
    action: string | null;
    status: string | null;
    finished_at: string | null;
  };
}

interface InboxSummary {
  ok: true;
  autonomy_mode: {
    mode: "ON" | "OFF";
    updated_at: string | null;
    updated_by: string | null;
  };
  canary_core_status: "PASS" | "FAIL" | "UNKNOWN";
  canary_optional_status: "PASS" | "WARN" | "UNKNOWN";
  scheduler_tick: {
    run_id: string | null;
    started_at: string | null;
    finished_at: string | null;
    observe_only: boolean | null;
    decisions_written: number | null;
    executed_written: number | null;
    mutating_candidates_blocked: number | null;
    tick_summary_url: string | null;
    proof_url: string | null;
  } | null;
  projects: ProjectSummary[];
}

interface AutonomyModeResponse {
  ok: true;
  mode: "ON" | "OFF";
  updated_at: string | null;
  updated_by: string | null;
  path: string;
}

interface ApprovalRecord {
  id: string;
  project_id: string;
  playbook_id: string;
  playbook_title: string;
  primary_action: string;
  status: "PENDING" | "APPROVED" | "REJECTED";
  rationale: string;
  created_at: string;
  created_by: string;
  resolved_at: string | null;
  resolved_by: string | null;
  note: string | null;
  proof_bundle: string;
  proof_bundle_url: string | null;
  request_path: string;
  request_url: string | null;
  resolution_path: string | null;
  resolution_url: string | null;
  policy_decision: "APPROVAL";
  autonomy_mode: "ON" | "OFF";
  run_id: string | null;
}

interface ApprovalsResponse {
  ok: true;
  approvals: ApprovalRecord[];
}

function cardToneClasses(tone: ApprovalCard["tone"]): string {
  if (tone === "danger") return "border-red-500/30 bg-red-500/10";
  if (tone === "warn") return "border-amber-500/30 bg-amber-500/10";
  return "border-white/10 bg-white/5";
}

function statusBadgeClasses(status: "PASS" | "FAIL" | "WARN" | "UNKNOWN"): string {
  if (status === "FAIL") return "border-red-500/30 bg-red-500/10 text-red-200";
  if (status === "WARN") return "border-amber-500/30 bg-amber-500/10 text-amber-200";
  if (status === "PASS") return "border-emerald-500/30 bg-emerald-500/10 text-emerald-200";
  return "border-white/10 bg-white/5 text-white/65";
}

function formatTimestamp(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

export default function InboxPage() {
  const token = useToken();
  const [summary, setSummary] = useState<InboxSummary | null>(null);
  const [autonomy, setAutonomy] = useState<AutonomyModeResponse | null>(null);
  const [approvals, setApprovals] = useState<ApprovalRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const load = async () => {
    const headers: Record<string, string> = {};
    if (token) headers["X-OpenClaw-Token"] = token;
    const [summaryRes, autonomyRes, approvalsRes] = await Promise.all([
      fetch("/api/ui/inbox_summary", { headers }),
      fetch("/api/ui/autonomy_mode", { headers }),
      fetch("/api/ui/approvals?status=PENDING", { headers }),
    ]);
    const [summaryData, autonomyData, approvalsData] = await Promise.all([
      summaryRes.json(),
      autonomyRes.json(),
      approvalsRes.json(),
    ]);

    if (!summaryRes.ok) throw new Error(summaryData.error || "Failed to load inbox summary");
    if (!autonomyRes.ok) throw new Error(autonomyData.error || "Failed to load autonomy mode");
    if (!approvalsRes.ok) throw new Error(approvalsData.error || "Failed to load approvals");

    setSummary(summaryData);
    setAutonomy(autonomyData);
    setApprovals((approvalsData as ApprovalsResponse).approvals ?? []);
  };

  useEffect(() => {
    startTransition(() => {
      void load()
        .catch((error) => setMessage(error instanceof Error ? error.message : "Failed to load inbox"))
        .finally(() => setLoading(false));
    });
  }, [token]);

  const cards = useMemo(
    () =>
      (summary?.projects ?? []).flatMap((project) =>
        project.cards.map((card) => ({ ...card, project_name: project.name }))
      ),
    [summary]
  );
  const humanGateProjects = useMemo(
    () => (summary?.projects ?? []).filter((project) => project.needs_human && project.human_gate),
    [summary]
  );
  const attentionCards = useMemo(
    () => cards.filter((card) => card.type !== "APPROVAL_REQUIRED" && card.type !== "HUMAN_ONLY"),
    [cards]
  );

  const mutateAutonomy = async (mode: "ON" | "OFF") => {
    setBusyKey(`autonomy-${mode}`);
    setMessage(null);
    try {
      const headers: Record<string, string> = { "Content-Type": "application/json" };
      if (token) headers["X-OpenClaw-Token"] = token;
      const res = await fetch("/api/ui/autonomy_mode", {
        method: "POST",
        headers,
        body: JSON.stringify({ mode }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Failed to update autonomy mode");
      await load();
      setMessage(`Autonomy mode ${data.mode}.`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Autonomy update failed");
    } finally {
      setBusyKey(null);
    }
  };

  const runPlaybook = async (projectId: string, playbookId: string) => {
    setBusyKey(`run-${projectId}`);
    setMessage(null);
    try {
      const headers: Record<string, string> = { "Content-Type": "application/json" };
      if (token) headers["X-OpenClaw-Token"] = token;
      const res = await fetch("/api/ui/playbooks/run", {
        method: "POST",
        headers,
        body: JSON.stringify({ project_id: projectId, playbook_id: playbookId, user_role: "admin" }),
      });
      const data = await res.json();
      if (!res.ok && res.status !== 202 && res.status !== 409) {
        throw new Error(data.message || data.error || "Run Next failed");
      }
      if (data.review_url) {
        window.location.href = data.review_url;
        return;
      }
      await load();
      setMessage(data.message || `${data.status}: ${data.playbook_run_id}`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Run Next failed");
    } finally {
      setBusyKey(null);
    }
  };

  const resolveApproval = async (approvalId: string, decision: "approve" | "reject") => {
    setBusyKey(`${decision}-${approvalId}`);
    setMessage(null);
    try {
      const headers: Record<string, string> = { "Content-Type": "application/json" };
      if (token) headers["X-OpenClaw-Token"] = token;
      const res = await fetch(`/api/ui/approvals/${encodeURIComponent(approvalId)}/${decision}`, {
        method: "POST",
        headers,
        body: JSON.stringify({ user_role: "admin" }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `Failed to ${decision} approval`);
      await load();
      setMessage(`Approval ${decision === "approve" ? "approved" : "rejected"}.`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : `Failed to ${decision} approval`);
    } finally {
      setBusyKey(null);
    }
  };

  if (loading && !summary) {
    return (
      <div className="glass-surface rounded-2xl p-12 text-center">
        <div className="inline-block h-6 w-6 animate-spin rounded-full border-2 border-blue-400 border-t-transparent" />
        <p className="mt-3 text-sm text-white/60">Loading Operator Inbox…</p>
      </div>
    );
  }

  const autonomyMode = autonomy?.mode ?? summary?.autonomy_mode.mode ?? "ON";
  const autonomyState = autonomy ?? summary?.autonomy_mode ?? null;

  return (
    <div data-testid="operator-inbox-page" className="space-y-8">
      <section className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <h2 className="text-2xl font-bold text-white/95 tracking-tight">Operator Inbox</h2>
          <p className="mt-1 text-sm text-white/60">
            Autonomy control, pending approvals, HUMAN_ONLY gates, and the next recommended playbook for each project.
          </p>
        </div>
        <GlassCard className="min-w-[18rem] p-4" data-testid="inbox-autonomy-card">
          <div className="flex items-center justify-between gap-4">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.24em] text-white/45">Autonomous Mode</p>
              <p className="mt-1 text-sm text-white/80">
                {autonomyState?.updated_at ? `${autonomyMode} · ${formatTimestamp(autonomyState.updated_at)}` : autonomyMode}
              </p>
              {autonomyState?.updated_by && (
                <p className="mt-1 text-xs text-white/45">Updated by {autonomyState.updated_by}</p>
              )}
            </div>
            <div className="inline-flex overflow-hidden rounded-xl border border-white/10">
              <button
                type="button"
                className={`px-3 py-2 text-xs font-semibold ${
                  autonomyMode === "ON" ? "bg-emerald-500/20 text-emerald-200" : "bg-white/5 text-white/60"
                }`}
                onClick={() => mutateAutonomy("ON")}
                disabled={busyKey !== null}
              >
                ON
              </button>
              <button
                type="button"
                className={`px-3 py-2 text-xs font-semibold ${
                  autonomyMode === "OFF" ? "bg-red-500/20 text-red-200" : "bg-white/5 text-white/60"
                }`}
                onClick={() => mutateAutonomy("OFF")}
                disabled={busyKey !== null}
              >
                OFF
              </button>
            </div>
          </div>
        </GlassCard>
      </section>

      {summary?.canary_core_status === "FAIL" && (
        <GlassCard data-testid="inbox-core-degraded-banner" className="border border-red-500/30 bg-red-500/10 p-4">
          <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
            <div>
              <p className="text-sm font-semibold text-red-200">Core canary degraded</p>
              <p className="mt-1 text-sm text-red-100/80">
                Only core degradation raises a red banner. Optional warnings remain amber pills.
              </p>
            </div>
            {summary.scheduler_tick?.proof_url && (
              <a
                href={summary.scheduler_tick.proof_url}
                className="inline-flex rounded-xl bg-red-500/20 px-3 py-2 text-sm font-medium text-red-100 hover:bg-red-500/30"
              >
                Open scheduler proof
              </a>
            )}
          </div>
        </GlassCard>
      )}

      {message && (
        <div className="rounded-xl border border-white/10 bg-white/5 px-4 py-3 text-sm text-white/80">
          {message}
        </div>
      )}

      <section className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1.2fr)_minmax(0,0.8fr)]">
        <GlassCard className="p-5" data-testid="inbox-scheduler-card">
          <div className="flex items-start justify-between gap-4">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.24em] text-white/45">Last Scheduler Tick</p>
              <p className="mt-2 text-lg font-semibold text-white/90">
                {summary?.scheduler_tick?.run_id ?? "No scheduler tick recorded"}
              </p>
              <div className="mt-3 space-y-1 text-sm text-white/60">
                <p>Finished: {formatTimestamp(summary?.scheduler_tick?.finished_at)}</p>
                <p>Started: {formatTimestamp(summary?.scheduler_tick?.started_at)}</p>
                <p>
                  Mode:{" "}
                  {summary?.scheduler_tick?.observe_only == null
                    ? "—"
                    : summary.scheduler_tick.observe_only
                      ? "Observe-only"
                      : "Execute"}
                </p>
                <p>
                  Decisions: {summary?.scheduler_tick?.decisions_written ?? "—"} · Executed:{" "}
                  {summary?.scheduler_tick?.executed_written ?? "—"}
                </p>
                <p>Blocked mutations: {summary?.scheduler_tick?.mutating_candidates_blocked ?? "—"}</p>
              </div>
            </div>
            <div className="flex flex-col items-end gap-2">
              <span
                className={`inline-flex rounded-full border px-2 py-0.5 text-[11px] font-medium ${statusBadgeClasses(
                  summary?.canary_core_status ?? "UNKNOWN"
                )}`}
              >
                Core {summary?.canary_core_status ?? "UNKNOWN"}
              </span>
              {summary?.canary_optional_status === "WARN" && (
                <span
                  data-testid="inbox-optional-warning"
                  className={`inline-flex rounded-full border px-2 py-0.5 text-[11px] font-medium ${statusBadgeClasses("WARN")}`}
                >
                  Optional warn
                </span>
              )}
            </div>
          </div>
          <div className="mt-4 flex flex-wrap gap-2">
            {summary?.scheduler_tick?.proof_url && (
              <a
                href={summary.scheduler_tick.proof_url}
                className="inline-flex rounded-xl bg-white/10 px-3 py-2 text-sm font-medium text-white/90 hover:bg-white/15"
              >
                Open proof bundle
              </a>
            )}
            {summary?.scheduler_tick?.tick_summary_url && (
              <a
                href={summary.scheduler_tick.tick_summary_url}
                className="inline-flex rounded-xl bg-white/5 px-3 py-2 text-sm font-medium text-white/75 hover:bg-white/10"
              >
                Tick summary JSON
              </a>
            )}
          </div>
        </GlassCard>

        <GlassCard className="p-5">
          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.24em] text-white/45">Queue Health</p>
              <p className="mt-2 text-lg font-semibold text-white/90">{approvals.length} pending approvals</p>
            </div>
            <GlassButton
              onClick={() => {
                setLoading(true);
                void load().finally(() => setLoading(false));
              }}
              size="sm"
              disabled={loading || busyKey !== null}
            >
              Refresh
            </GlassButton>
          </div>
          <div className="mt-4 grid grid-cols-2 gap-3">
            <div className="rounded-2xl border border-white/10 bg-white/5 p-4">
              <p className="text-xs text-white/45">HUMAN_ONLY gates</p>
              <p className="mt-2 text-2xl font-semibold text-white/90">{humanGateProjects.length}</p>
            </div>
            <div className="rounded-2xl border border-white/10 bg-white/5 p-4">
              <p className="text-xs text-white/45">Projects tracked</p>
              <p className="mt-2 text-2xl font-semibold text-white/90">{summary?.projects.length ?? 0}</p>
            </div>
          </div>
          <div className="mt-4 flex flex-wrap gap-2 text-xs text-white/55">
            <span className="rounded-full bg-white/5 px-2 py-1">Mode {autonomyMode}</span>
            <span className="rounded-full bg-white/5 px-2 py-1">
              Optional {summary?.canary_optional_status ?? "UNKNOWN"}
            </span>
          </div>
        </GlassCard>
      </section>

      <section>
        <div className="mb-3 flex items-center justify-between">
          <h3 className="text-lg font-semibold text-white/90">Pending Approvals</h3>
          <span className="text-xs text-white/45">{approvals.length} items</span>
        </div>
        {approvals.length === 0 ? (
          <GlassCard>
            <div className="p-6 text-center text-sm text-white/50">No pending approvals.</div>
          </GlassCard>
        ) : (
          <div className="space-y-3" data-testid="approvals-list">
            {approvals.map((approval) => {
              const project = summary?.projects.find((item) => item.project_id === approval.project_id);
              return (
                <GlassCard key={approval.id} className="border border-amber-500/20 bg-amber-500/5">
                  <div className="space-y-3 p-4">
                    <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                      <div>
                        <p className="text-sm font-semibold text-white/95">{approval.playbook_title}</p>
                        <p className="mt-1 text-xs text-white/55">
                          {project?.name ?? approval.project_id} · {approval.primary_action}
                        </p>
                        <p className="mt-2 text-sm text-white/75">{approval.rationale}</p>
                      </div>
                      <div className="text-xs text-white/50">
                        <p>Created {formatTimestamp(approval.created_at)}</p>
                        <p>{approval.autonomy_mode} mode</p>
                      </div>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      {approval.request_url && (
                        <a
                          href={approval.request_url}
                          className="inline-flex items-center rounded-lg bg-white/5 px-2.5 py-1.5 text-xs font-medium text-white/70 hover:bg-white/10"
                        >
                          Request
                        </a>
                      )}
                      {approval.proof_bundle_url && (
                        <a
                          href={approval.proof_bundle_url}
                          className="inline-flex items-center rounded-lg bg-white/5 px-2.5 py-1.5 text-xs font-medium text-white/70 hover:bg-white/10"
                        >
                          Proof
                        </a>
                      )}
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <button
                        type="button"
                        className="rounded-xl bg-emerald-500/20 px-3 py-2 text-sm font-medium text-emerald-200 hover:bg-emerald-500/30 disabled:opacity-60"
                        onClick={() => resolveApproval(approval.id, "approve")}
                        disabled={busyKey !== null}
                      >
                        Approve
                      </button>
                      <button
                        type="button"
                        className="rounded-xl bg-red-500/20 px-3 py-2 text-sm font-medium text-red-200 hover:bg-red-500/30 disabled:opacity-60"
                        onClick={() => resolveApproval(approval.id, "reject")}
                        disabled={busyKey !== null}
                      >
                        Reject
                      </button>
                    </div>
                  </div>
                </GlassCard>
              );
            })}
          </div>
        )}
      </section>

      <section>
        <div className="mb-3 flex items-center justify-between">
          <h3 className="text-lg font-semibold text-white/90">HUMAN_ONLY Gates</h3>
          <span className="text-xs text-white/45">{humanGateProjects.length} open</span>
        </div>
        {humanGateProjects.length === 0 ? (
          <GlassCard>
            <div className="p-6 text-center text-sm text-white/50">No HUMAN_ONLY gates are currently open.</div>
          </GlassCard>
        ) : (
          <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
            {humanGateProjects.map((project) => (
              <GlassCard key={project.project_id} className="border border-amber-500/20 bg-amber-500/5 p-5">
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <p className="text-sm font-semibold text-white/95">{project.name}</p>
                    <p className="mt-1 text-sm text-white/65">
                      {project.human_gate?.instruction ?? "Human intervention required."}
                    </p>
                    <p className="mt-3 text-xs text-white/45">
                      Last run {project.last_run.run_id ?? "—"} · {project.last_run.status ?? "—"}
                    </p>
                  </div>
                  <Link href={`/projects/${project.project_id}`} className="text-xs text-blue-300 hover:text-blue-200">
                    Open project
                  </Link>
                </div>
                <div className="mt-4 flex flex-wrap gap-2">
                  {project.human_gate?.browser_url && (
                    <a
                      href={project.human_gate.browser_url}
                      className="inline-flex rounded-xl bg-white/10 px-3 py-2 text-sm font-medium text-white/90 hover:bg-white/15"
                    >
                      Open Browser
                    </a>
                  )}
                  {project.human_gate?.novnc_url && (
                    <a
                      href={project.human_gate.novnc_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex rounded-xl bg-white/10 px-3 py-2 text-sm font-medium text-white/90 hover:bg-white/15"
                    >
                      Open noVNC
                    </a>
                  )}
                  {project.proof_links.slice(0, 2).map((proof) => (
                    <a
                      key={`${project.project_id}-${proof.href}`}
                      href={proof.href}
                      className="inline-flex rounded-xl bg-white/5 px-3 py-2 text-sm font-medium text-white/75 hover:bg-white/10"
                    >
                      {proof.label}
                    </a>
                  ))}
                </div>
              </GlassCard>
            ))}
          </div>
        )}
      </section>

      {attentionCards.length > 0 && (
        <section>
          <div className="mb-3 flex items-center justify-between">
            <h3 className="text-lg font-semibold text-white/90">Alerts</h3>
            <span className="text-xs text-white/45">{attentionCards.length} items</span>
          </div>
          <div className="space-y-3">
            {attentionCards.map((card) => (
              <GlassCard key={card.id} className={`border ${cardToneClasses(card.tone)}`}>
                <div className="space-y-3 p-4">
                  <div className="flex flex-col gap-2 md:flex-row md:items-start md:justify-between">
                    <div>
                      <p className="text-sm font-semibold text-white/95">{card.title}</p>
                      <p className="mt-1 text-xs text-white/55">{card.project_name}</p>
                      <p className="mt-2 text-sm text-white/75">{card.summary}</p>
                    </div>
                    {card.action_href && (
                      <a
                        href={card.action_href}
                        target={card.action_href.startsWith("http") ? "_blank" : undefined}
                        rel={card.action_href.startsWith("http") ? "noopener noreferrer" : undefined}
                        className="inline-flex items-center rounded-xl bg-white/10 px-3 py-2 text-sm font-medium text-white/90 hover:bg-white/15"
                      >
                        {card.action_label || "Open"}
                      </a>
                    )}
                  </div>
                  {card.proof_links.length > 0 && (
                    <div className="flex flex-wrap gap-2">
                      {card.proof_links.map((proof) => (
                        <a
                          key={`${card.id}-${proof.href}`}
                          href={proof.href}
                          target={proof.href.startsWith("http") ? "_blank" : undefined}
                          rel={proof.href.startsWith("http") ? "noopener noreferrer" : undefined}
                          className="inline-flex items-center rounded-lg bg-white/5 px-2.5 py-1.5 text-xs font-medium text-white/70 hover:bg-white/10"
                        >
                          {proof.label}
                        </a>
                      ))}
                    </div>
                  )}
                </div>
              </GlassCard>
            ))}
          </div>
        </section>
      )}

      <section data-testid="run-next-grid">
        <h3 className="mb-3 text-lg font-semibold text-white/90">Run Next</h3>
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
          {(summary?.projects ?? []).map((project) => (
            <GlassCard key={project.project_id} className="p-5">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <p className="text-sm font-semibold text-white/95">{project.name}</p>
                  <p className="mt-1 text-sm text-white/60">{project.description}</p>
                </div>
                <div className="flex items-center gap-2">
                  {project.optional_status === "WARN" && (
                    <span className="rounded-full border border-amber-500/30 bg-amber-500/10 px-2 py-0.5 text-[11px] font-medium text-amber-200">
                      Optional warn
                    </span>
                  )}
                  {project.needs_human && (
                    <span className="rounded-full border border-amber-500/30 bg-amber-500/10 px-2 py-0.5 text-[11px] font-medium text-amber-200">
                      HUMAN_ONLY
                    </span>
                  )}
                  {project.approvals_pending > 0 && (
                    <span className="rounded-full border border-white/10 bg-white/5 px-2 py-0.5 text-[11px] font-medium text-white/70">
                      {project.approvals_pending} pending
                    </span>
                  )}
                  <Link href={`/projects/${project.project_id}`} className="text-xs text-blue-300 hover:text-blue-200">
                    Open project
                  </Link>
                </div>
              </div>
              <div className="mt-4 rounded-xl border border-white/10 bg-white/5 p-4">
                {project.recommended_playbook ? (
                  <>
                    <div className="flex items-center justify-between gap-4">
                      <div>
                        <p className="text-sm font-semibold text-white/90">{project.recommended_playbook.title}</p>
                        <p className="mt-1 text-sm text-white/65">{project.recommended_playbook.rationale}</p>
                      </div>
                      <button
                        type="button"
                        className="rounded-xl bg-blue-500/20 px-3 py-2 text-sm font-medium text-blue-200 hover:bg-blue-500/30 disabled:opacity-60"
                        onClick={() => runPlaybook(project.project_id, project.recommended_playbook!.id)}
                        disabled={busyKey !== null}
                      >
                        Run Next
                      </button>
                    </div>
                    {project.recommended_playbook.expected_outputs.length > 0 && (
                      <div className="mt-3 flex flex-wrap gap-2">
                        {project.recommended_playbook.expected_outputs.map((item) => (
                          <span
                            key={`${project.project_id}-${item}`}
                            className="rounded-full bg-white/5 px-2 py-0.5 text-[11px] text-white/55"
                          >
                            {item}
                          </span>
                        ))}
                      </div>
                    )}
                  </>
                ) : (
                  <p className="text-sm text-white/55">No playbook recommendation available.</p>
                )}
              </div>
            </GlassCard>
          ))}
        </div>
      </section>
    </div>
  );
}
