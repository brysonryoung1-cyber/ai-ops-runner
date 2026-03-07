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
}

interface InboxSummary {
  autonomy_mode: {
    mode: "ON" | "OFF";
    updated_at: string | null;
    updated_by: string | null;
  };
  canary_core_status: "PASS" | "FAIL" | "UNKNOWN";
  canary_optional_status: "PASS" | "WARN" | "UNKNOWN";
  projects: ProjectSummary[];
}

function toneClasses(tone: ApprovalCard["tone"]): string {
  if (tone === "danger") return "border-red-500/30 bg-red-500/10";
  if (tone === "warn") return "border-amber-500/30 bg-amber-500/10";
  return "border-white/10 bg-white/5";
}

export default function InboxPage() {
  const token = useToken();
  const [summary, setSummary] = useState<InboxSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const load = async () => {
    const headers: Record<string, string> = {};
    if (token) headers["X-OpenClaw-Token"] = token;
    const res = await fetch("/api/ui/inbox_summary", { headers });
    const data = await res.json();
    setSummary(data);
  };

  useEffect(() => {
    startTransition(() => {
      load()
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
      setMessage(`Approval ${decision}d.`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : `Failed to ${decision} approval`);
    } finally {
      setBusyKey(null);
    }
  };

  if (loading && !summary) {
    return (
      <div className="glass-surface rounded-2xl p-12 text-center">
        <div className="inline-block w-6 h-6 border-2 border-blue-400 border-t-transparent rounded-full animate-spin" />
        <p className="text-sm text-white/60 mt-3">Loading Operator Inbox…</p>
      </div>
    );
  }

  const autonomyMode = summary?.autonomy_mode.mode ?? "ON";

  return (
    <div data-testid="operator-inbox-page" className="space-y-8">
      <section className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <h2 className="text-2xl font-bold text-white/95 tracking-tight">Operator Inbox</h2>
          <p className="text-sm text-white/60 mt-1">
            Human gates, approval cards, core degradation, and the single recommended next playbook.
          </p>
        </div>
        <GlassCard className="p-4 min-w-[18rem]">
          <div className="flex items-center justify-between gap-4">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.24em] text-white/45">Autonomous Mode</p>
              <p className="text-sm text-white/80 mt-1">
                {summary?.autonomy_mode.updated_at
                  ? `${autonomyMode} · ${summary.autonomy_mode.updated_at}`
                  : autonomyMode}
              </p>
            </div>
            <div className="inline-flex rounded-xl border border-white/10 overflow-hidden">
              <button
                type="button"
                className={`px-3 py-2 text-xs font-semibold ${autonomyMode === "ON" ? "bg-emerald-500/20 text-emerald-200" : "bg-white/5 text-white/60"}`}
                onClick={() => mutateAutonomy("ON")}
                disabled={busyKey !== null}
              >
                ON
              </button>
              <button
                type="button"
                className={`px-3 py-2 text-xs font-semibold ${autonomyMode === "OFF" ? "bg-red-500/20 text-red-200" : "bg-white/5 text-white/60"}`}
                onClick={() => mutateAutonomy("OFF")}
                disabled={busyKey !== null}
              >
                OFF
              </button>
            </div>
          </div>
        </GlassCard>
      </section>

      {message && (
        <div className="rounded-xl border border-white/10 bg-white/5 px-4 py-3 text-sm text-white/80">
          {message}
        </div>
      )}

      <section>
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-lg font-semibold text-white/90">Action Queue</h3>
          <GlassButton
            onClick={() => {
              setLoading(true);
              load().finally(() => setLoading(false));
            }}
            size="sm"
            disabled={loading || busyKey !== null}
          >
            Refresh
          </GlassButton>
        </div>
        {cards.length === 0 ? (
          <GlassCard>
            <div className="p-6 text-center text-white/50 text-sm">No open human gates, approvals, or degraded core alerts.</div>
          </GlassCard>
        ) : (
          <div className="space-y-3">
            {cards.map((card) => (
              <GlassCard key={card.id} className={`border ${toneClasses(card.tone)}`}>
                <div className="p-4 space-y-3">
                  <div className="flex flex-col gap-2 md:flex-row md:items-start md:justify-between">
                    <div>
                      <p className="text-sm font-semibold text-white/95">{card.title}</p>
                      <p className="text-xs text-white/55 mt-1">{card.project_name}</p>
                      <p className="text-sm text-white/75 mt-2">{card.summary}</p>
                    </div>
                    {card.action_href && (
                      <a
                        href={card.action_href}
                        target={card.action_href.startsWith("http") ? "_blank" : undefined}
                        rel={card.action_href.startsWith("http") ? "noopener noreferrer" : undefined}
                        className="inline-flex items-center rounded-xl px-3 py-2 text-sm font-medium bg-white/10 text-white/90 hover:bg-white/15"
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
                          className="inline-flex items-center rounded-lg px-2.5 py-1.5 text-xs font-medium bg-white/5 text-white/70 hover:bg-white/10"
                        >
                          {proof.label}
                        </a>
                      ))}
                    </div>
                  )}
                  {card.type === "APPROVAL_REQUIRED" && card.approval_id && (
                    <div className="flex flex-wrap gap-2">
                      <button
                        type="button"
                        className="rounded-xl bg-emerald-500/20 px-3 py-2 text-sm font-medium text-emerald-200 hover:bg-emerald-500/30 disabled:opacity-60"
                        onClick={() => resolveApproval(card.approval_id!, "approve")}
                        disabled={busyKey !== null}
                      >
                        Approve
                      </button>
                      <button
                        type="button"
                        className="rounded-xl bg-red-500/20 px-3 py-2 text-sm font-medium text-red-200 hover:bg-red-500/30 disabled:opacity-60"
                        onClick={() => resolveApproval(card.approval_id!, "reject")}
                        disabled={busyKey !== null}
                      >
                        Reject
                      </button>
                    </div>
                  )}
                </div>
              </GlassCard>
            ))}
          </div>
        )}
      </section>

      <section>
        <h3 className="text-lg font-semibold text-white/90 mb-3">Run Next</h3>
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
          {(summary?.projects ?? []).map((project) => (
            <GlassCard key={project.project_id} className="p-5">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <p className="text-sm font-semibold text-white/95">{project.name}</p>
                  <p className="text-sm text-white/60 mt-1">{project.description}</p>
                </div>
                <div className="flex items-center gap-2">
                  {project.optional_status === "WARN" && (
                    <span className="rounded-full border border-amber-500/30 bg-amber-500/10 px-2 py-0.5 text-[11px] font-medium text-amber-200">
                      Optional warn
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
                        <p className="text-sm text-white/65 mt-1">{project.recommended_playbook.rationale}</p>
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
                          <span key={`${project.project_id}-${item}`} className="rounded-full bg-white/5 px-2 py-0.5 text-[11px] text-white/55">
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
