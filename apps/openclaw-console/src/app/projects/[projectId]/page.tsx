"use client";

import { startTransition, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { GlassCard } from "@/components/glass";
import { useToken } from "@/lib/token-context";

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
  last_run: {
    run_id: string | null;
    action: string | null;
    status: string | null;
    finished_at: string | null;
  };
}

interface InboxResponse {
  autonomy_mode: {
    mode: "ON" | "OFF";
    updated_at: string | null;
    updated_by: string | null;
  };
  projects: ProjectSummary[];
}

function statusTone(project: ProjectSummary): { label: string; className: string } {
  if (project.core_status === "FAIL") {
    return { label: "Core degraded", className: "text-red-300" };
  }
  if (project.needs_human) {
    return { label: "Human gate open", className: "text-amber-300" };
  }
  if (project.approvals_pending > 0) {
    return { label: "Approval pending", className: "text-amber-300" };
  }
  return { label: "Ready", className: "text-emerald-300" };
}

export default function ProjectDetailsPage() {
  const params = useParams();
  const token = useToken();
  const projectId = typeof params.projectId === "string" ? params.projectId : "";
  const [project, setProject] = useState<ProjectSummary | null>(null);
  const [summary, setSummary] = useState<InboxResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const load = async () => {
    const headers: Record<string, string> = {};
    if (token) headers["X-OpenClaw-Token"] = token;
    const res = await fetch(`/api/ui/inbox_summary?project_id=${encodeURIComponent(projectId)}`, { headers });
    const data = await res.json();
    setSummary(data);
    setProject(data.projects?.[0] ?? null);
  };

  useEffect(() => {
    if (!projectId) return;
    startTransition(() => {
      load()
        .catch((error) => setMessage(error instanceof Error ? error.message : "Failed to load project"))
        .finally(() => setLoading(false));
    });
  }, [projectId, token]);

  const extraPlaybooks = useMemo(() => {
    if (!project) return [];
    return project.playbooks
      .filter((playbook) => playbook.id !== project.recommended_playbook?.id && !playbook.id.endsWith(".review_approvals"))
      .slice(0, 3);
  }, [project]);

  const mutateAutonomy = async (mode: "ON" | "OFF") => {
    setBusy(`autonomy-${mode}`);
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
      setMessage(error instanceof Error ? error.message : "Failed to update autonomy mode");
    } finally {
      setBusy(null);
    }
  };

  const runPlaybook = async (playbookId: string, confirmPhrase?: string) => {
    setBusy(playbookId);
    setMessage(null);
    try {
      const headers: Record<string, string> = { "Content-Type": "application/json" };
      if (token) headers["X-OpenClaw-Token"] = token;
      const res = await fetch("/api/ui/playbooks/run", {
        method: "POST",
        headers,
        body: JSON.stringify({
          project_id: projectId,
          playbook_id: playbookId,
          user_role: "admin",
          ...(confirmPhrase ? { confirm_phrase: confirmPhrase } : {}),
        }),
      });
      const data = await res.json();
      if (res.status === 409 && data.error_class === "BREAK_GLASS_REQUIRED") {
        const value = window.prompt('Type "RUN" to confirm break-glass execution.');
        if (value === "RUN") {
          await runPlaybook(playbookId, "RUN");
        }
        return;
      }
      if (!res.ok && res.status !== 202) {
        throw new Error(data.message || data.error || "Playbook failed");
      }
      if (data.review_url) {
        window.location.href = data.review_url;
        return;
      }
      await load();
      setMessage(data.message || `${data.status}: ${data.playbook_run_id}`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Playbook failed");
    } finally {
      setBusy(null);
    }
  };

  if (loading && !project) {
    return (
      <div className="glass-surface rounded-2xl p-12 text-center">
        <div className="inline-block w-6 h-6 border-2 border-blue-400 border-t-transparent rounded-full animate-spin" />
        <p className="text-sm text-white/60 mt-3">Loading project…</p>
      </div>
    );
  }

  if (!project) {
    return (
      <div className="rounded-2xl border border-red-500/20 bg-red-500/10 p-6">
        <p className="text-sm font-semibold text-red-200">Project not found</p>
        <Link href="/projects" className="mt-3 inline-flex text-sm text-blue-300 hover:text-blue-200">
          Back to Projects
        </Link>
      </div>
    );
  }

  const tone = statusTone(project);

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <Link href="/projects" className="text-xs text-blue-300 hover:text-blue-200">
            ← Projects
          </Link>
          <h2 className="text-2xl font-bold text-white/95 tracking-tight mt-2">{project.name}</h2>
          <p className="text-sm text-white/60 mt-1">{project.description}</p>
        </div>
        <div className="text-right">
          <p className={`text-sm font-semibold ${tone.className}`}>{tone.label}</p>
          {project.optional_status === "WARN" && (
            <span
              data-testid="project-optional-warning"
              className="mt-2 inline-flex rounded-full border border-amber-500/30 bg-amber-500/10 px-2 py-0.5 text-[11px] font-medium text-amber-200"
            >
              Optional checks warning
            </span>
          )}
        </div>
      </div>

      {message && (
        <div className="rounded-xl border border-white/10 bg-white/5 px-4 py-3 text-sm text-white/80">
          {message}
        </div>
      )}

      {project.core_status === "FAIL" && (
        <GlassCard data-testid="project-core-degraded-banner" className="border border-red-500/30 bg-red-500/10 p-4">
          <p className="text-sm font-semibold text-red-200">Core degraded</p>
          <p className="text-sm text-red-100/80 mt-1">
            Optional warnings stay amber; only core failures raise the red banner.
          </p>
        </GlassCard>
      )}

      <GlassCard className="p-5" data-testid="project-primary-actions">
        <div className="flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between">
          <div className="space-y-4">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.24em] text-white/45">Autonomous Mode</p>
              <div className="mt-3" data-testid="project-default-buttons">
                <button
                  type="button"
                  className={`rounded-xl px-3 py-2 text-xs font-semibold ${
                    summary?.autonomy_mode.mode === "ON"
                      ? "bg-emerald-500/20 text-emerald-200"
                      : "bg-red-500/20 text-red-200"
                  }`}
                  onClick={() => mutateAutonomy(summary?.autonomy_mode.mode === "ON" ? "OFF" : "ON")}
                  disabled={busy !== null}
                >
                  {summary?.autonomy_mode.mode === "ON" ? "Autonomy ON" : "Autonomy OFF"}
                </button>
              </div>
            </div>
            {project.recommended_playbook && (
              <div className="rounded-2xl border border-white/10 bg-white/5 p-4">
                <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                  <div>
                    <p className="text-sm font-semibold text-white/90">{project.recommended_playbook.title}</p>
                    <p className="text-sm text-white/65 mt-1">{project.recommended_playbook.rationale}</p>
                  </div>
                  <button
                    type="button"
                    className="rounded-xl bg-blue-500/20 px-3 py-2 text-sm font-medium text-blue-200 hover:bg-blue-500/30 disabled:opacity-60"
                    onClick={() => runPlaybook(project.recommended_playbook!.id)}
                    disabled={busy !== null}
                  >
                    Run Next
                  </button>
                </div>
              </div>
            )}
            {project.approvals_pending > 0 && (
              <p className="text-sm text-amber-200">
                {project.approvals_pending} approval {project.approvals_pending === 1 ? "item" : "items"} pending.
                <Link href="/inbox" className="ml-2 text-blue-300 hover:text-blue-200">
                  Open Inbox
                </Link>
              </p>
            )}
            {project.needs_human && project.human_gate && (
              <div className="rounded-2xl border border-amber-500/30 bg-amber-500/10 p-4 text-sm text-amber-100/90">
                <p className="font-semibold text-amber-200">Human gate open</p>
                {project.human_gate.instruction && <p className="mt-2">{project.human_gate.instruction}</p>}
                <div className="mt-3 flex flex-wrap gap-2">
                  {project.human_gate.browser_url && (
                    <a
                      href={project.human_gate.browser_url}
                      className="inline-flex rounded-lg bg-white/10 px-3 py-2 text-xs font-medium text-white/90 hover:bg-white/15"
                    >
                      Open Browser
                    </a>
                  )}
                  {project.human_gate.novnc_url && (
                    <a
                      href={project.human_gate.novnc_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex rounded-lg bg-white/10 px-3 py-2 text-xs font-medium text-white/90 hover:bg-white/15"
                    >
                      Open noVNC
                    </a>
                  )}
                </div>
              </div>
            )}
          </div>

          <div className="lg:max-w-md lg:w-full">
            <p className="text-xs font-semibold uppercase tracking-[0.24em] text-white/45">Proofs</p>
            <div className="mt-3 flex flex-wrap gap-2">
              {project.proof_links.length > 0 ? (
                project.proof_links.map((proof) => (
                  <a
                    key={`${project.project_id}-${proof.href}`}
                    href={proof.href}
                    className="inline-flex rounded-lg bg-white/5 px-3 py-2 text-xs font-medium text-white/75 hover:bg-white/10"
                  >
                    {proof.label}
                  </a>
                ))
              ) : (
                <span className="text-sm text-white/45">No proof links available yet.</span>
              )}
            </div>
            <div className="mt-4 text-sm text-white/60 space-y-1">
              <p>Last run: {project.last_run.run_id ?? "—"}</p>
              <p>Action: {project.last_run.action ?? "—"}</p>
              <p>Status: {project.last_run.status ?? "—"}</p>
              {project.business_dod_pass != null && (
                <p>Business DoD: {project.business_dod_pass ? "PASS" : "FAIL"}</p>
              )}
            </div>
          </div>
        </div>
      </GlassCard>

      <GlassCard className="p-5">
        <div className="flex items-center justify-between gap-4 mb-4">
          <div>
            <p className="text-sm font-semibold text-white/90">Playbooks</p>
            <p className="text-sm text-white/60 mt-1">Raw actions stay in Advanced. This page only shows the primary playbook surface.</p>
          </div>
          <Link href={`/advanced/catalog?project=${encodeURIComponent(project.project_id)}`} className="text-sm text-blue-300 hover:text-blue-200">
            Open Catalog
          </Link>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3" data-testid="project-playbook-buttons">
          {extraPlaybooks.map((playbook) => (
            <button
              key={playbook.id}
              type="button"
              className="rounded-2xl border border-white/10 bg-white/5 p-4 text-left hover:bg-white/10 disabled:opacity-60"
              onClick={() => runPlaybook(playbook.id)}
              disabled={busy !== null}
            >
              <div className="flex items-center justify-between gap-2">
                <p className="text-sm font-semibold text-white/90">{playbook.title}</p>
                <span className="rounded-full bg-white/5 px-2 py-0.5 text-[10px] text-white/55">
                  {playbook.policy_preview}
                </span>
              </div>
              <p className="text-sm text-white/60 mt-2">{playbook.description}</p>
            </button>
          ))}
        </div>
        <details className="mt-4 rounded-2xl border border-white/10 bg-white/5 p-4">
          <summary className="cursor-pointer text-sm font-semibold text-white/80">Advanced</summary>
          <div className="mt-3 space-y-2 text-sm text-white/60">
            <p>Catalog includes raw executor actions, search, tags, and risk badges.</p>
            <Link href={`/advanced/catalog?project=${encodeURIComponent(project.project_id)}`} className="text-blue-300 hover:text-blue-200">
              Go to Advanced Catalog
            </Link>
          </div>
        </details>
      </GlassCard>
    </div>
  );
}
