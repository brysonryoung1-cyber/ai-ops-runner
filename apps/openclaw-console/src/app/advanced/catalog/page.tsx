"use client";

import { Suspense, startTransition, useDeferredValue, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { getAllPlaybooks } from "@/lib/plugins";
import { riskLabel } from "@/lib/playbooks";
import { useExec } from "@/lib/hooks";
import { useToken } from "@/lib/token-context";

type PolicyTier = "readonly" | "low_risk_ops" | "privileged_ops" | "destructive_ops";

interface RawAction {
  id: string;
  title: string;
  description: string;
  tags: string[];
  tier: PolicyTier;
}

const RAW_ACTIONS: RawAction[] = [
  {
    id: "openclaw_hq_audit",
    title: "HQ Audit",
    description: "Deterministic infrastructure audit plus self-heal loop.",
    tags: ["infra", "doctor", "heal"],
    tier: "low_risk_ops",
  },
  {
    id: "openclaw_novnc_doctor",
    title: "noVNC Doctor",
    description: "Framebuffer-aware noVNC doctor.",
    tags: ["soma", "novnc", "doctor"],
    tier: "low_risk_ops",
  },
  {
    id: "soma_kajabi_session_check",
    title: "Soma Session Check",
    description: "Read-only Kajabi session readiness check.",
    tags: ["soma", "readonly", "session"],
    tier: "readonly",
  },
  {
    id: "pred_markets.report.health",
    title: "Prediction Markets Health",
    description: "Read-only mirror health report.",
    tags: ["pred", "readonly", "health"],
    tier: "readonly",
  },
  {
    id: "deploy_and_verify",
    title: "Deploy and Verify",
    description: "Production deploy plus verification. Break-glass only.",
    tags: ["infra", "deploy", "production"],
    tier: "destructive_ops",
  },
];

function rawTierLabel(tier: PolicyTier): string {
  if (tier === "destructive_ops") return "BREAK_GLASS";
  if (tier === "privileged_ops") return "APPROVAL";
  if (tier === "readonly") return "AUTO";
  return "AUTO";
}

function rawRiskLabel(tier: PolicyTier): string {
  if (tier === "destructive_ops") return "High risk";
  if (tier === "privileged_ops") return "Medium risk";
  return "Low risk";
}

function badgeClasses(tone: "low" | "med" | "high"): string {
  if (tone === "high") return "border-red-500/30 bg-red-500/10 text-red-200";
  if (tone === "med") return "border-amber-500/30 bg-amber-500/10 text-amber-200";
  return "border-emerald-500/30 bg-emerald-500/10 text-emerald-200";
}

function matchesFilter(text: string, terms: string[]): boolean {
  return terms.every((term) => text.includes(term));
}

function CatalogPageContent() {
  const token = useToken();
  const searchParams = useSearchParams();
  const initialProject = searchParams.get("project") || "";
  const [query, setQuery] = useState(initialProject);
  const [selectedTags, setSelectedTags] = useState<string[]>(initialProject ? [initialProject] : []);
  const [message, setMessage] = useState<string | null>(null);
  const [confirmTarget, setConfirmTarget] = useState<{
    type: "playbook" | "action";
    id: string;
    label: string;
    phrase: string;
    reason: string;
  } | null>(null);
  const [confirmValue, setConfirmValue] = useState("");
  const deferredQuery = useDeferredValue(query.trim().toLowerCase());
  const terms = deferredQuery.split(/\s+/).filter(Boolean);
  const { exec, loading } = useExec();

  const playbooks = useMemo(() => getAllPlaybooks(), []);
  const allTags = useMemo(() => {
    const tags = new Set<string>();
    for (const playbook of playbooks) {
      tags.add(playbook.project_id);
      for (const tag of playbook.tags) tags.add(tag);
    }
    for (const action of RAW_ACTIONS) {
      for (const tag of action.tags) tags.add(tag);
    }
    return Array.from(tags).sort((a, b) => a.localeCompare(b));
  }, [playbooks]);

  const filteredPlaybooks = useMemo(() => {
    return playbooks.filter((playbook) => {
      const haystack = `${playbook.project_id} ${playbook.title} ${playbook.description} ${playbook.tags.join(" ")}`.toLowerCase();
      const matchesTerms = terms.length === 0 || matchesFilter(haystack, terms);
      const matchesTags =
        selectedTags.length === 0 ||
        selectedTags.every((tag) => playbook.project_id === tag || playbook.tags.includes(tag));
      return matchesTerms && matchesTags;
    });
  }, [playbooks, selectedTags, terms]);

  const filteredActions = useMemo(() => {
    return RAW_ACTIONS.filter((action) => {
      const haystack = `${action.title} ${action.description} ${action.tags.join(" ")}`.toLowerCase();
      const matchesTerms = terms.length === 0 || matchesFilter(haystack, terms);
      const matchesTags = selectedTags.length === 0 || selectedTags.every((tag) => action.tags.includes(tag));
      return matchesTerms && matchesTags;
    });
  }, [selectedTags, terms]);

  const runPlaybook = async (playbookId: string, confirmPhrase?: string) => {
    setMessage(null);
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (token) headers["X-OpenClaw-Token"] = token;
    const playbook = playbooks.find((item) => item.id === playbookId);
    if (!playbook) return;
    const res = await fetch("/api/ui/playbooks/run", {
      method: "POST",
      headers,
      body: JSON.stringify({
        project_id: playbook.project_id,
        playbook_id: playbook.id,
        user_role: "admin",
        ...(confirmPhrase ? { confirm_phrase: confirmPhrase } : {}),
      }),
    });
    const data = await res.json();
    if (res.status === 409 && data.error_class === "BREAK_GLASS_REQUIRED") {
      setConfirmTarget({
        type: "playbook",
        id: playbook.id,
        label: playbook.title,
        phrase: "RUN",
        reason: "Break-glass playbooks require the RUN confirm phrase before execution.",
      });
      setConfirmValue("");
      return;
    }
    setMessage(data.message || `${data.status}: ${data.playbook_run_id ?? playbook.id}`);
  };

  const runAction = async (actionId: string, requiresConfirm: boolean) => {
    if (requiresConfirm) {
      const action = RAW_ACTIONS.find((item) => item.id === actionId);
      setConfirmTarget({
        type: "action",
        id: actionId,
        label: action?.title || actionId,
        phrase: "RUN",
        reason: "Privileged executor actions require a typed confirmation before dispatch.",
      });
      setConfirmValue("");
      return;
    }
    const result = await exec(actionId);
    setMessage(result.error || `${actionId}: ${result.ok ? "started" : "failed"}`);
  };

  const submitConfirm = async () => {
    if (!confirmTarget || confirmValue !== confirmTarget.phrase) return;
    const target = confirmTarget;
    setConfirmTarget(null);
    if (target.type === "playbook") {
      await runPlaybook(target.id, "RUN");
      return;
    }
    const result = await exec(target.id);
    setMessage(result.error || `${target.label}: ${result.ok ? "started" : "failed"}`);
  };

  const toggleTag = (tag: string) => {
    startTransition(() => {
      setSelectedTags((current) =>
        current.includes(tag) ? current.filter((value) => value !== tag) : [...current, tag]
      );
    });
  };

  return (
    <div className="space-y-8">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.24em] text-white/45">Advanced</p>
          <h2 className="mt-2 text-2xl font-bold tracking-tight text-white/95">Catalog</h2>
          <p className="mt-1 text-sm text-white/60">
            Search playbooks and executor actions, then narrow with tag filters before dispatching anything privileged.
          </p>
        </div>
        <input
          value={query}
          onChange={(event) => startTransition(() => setQuery(event.target.value))}
          placeholder="Search project, tag, playbook, or action"
          className="w-full rounded-2xl border border-white/10 bg-white/5 px-4 py-3 text-sm text-white outline-none placeholder:text-white/35 focus:border-blue-400/50 lg:w-80"
        />
      </div>

      {message && (
        <div className="rounded-xl border border-white/10 bg-white/5 px-4 py-3 text-sm text-white/80">
          {message}
        </div>
      )}

      <section className="space-y-3">
        <div className="flex items-center justify-between gap-4">
          <h3 className="text-lg font-semibold text-white/90">Tag Filters</h3>
          <button
            type="button"
            className="text-xs text-white/45 hover:text-white/75 disabled:opacity-50"
            onClick={() => setSelectedTags([])}
            disabled={selectedTags.length === 0}
          >
            Clear filters
          </button>
        </div>
        <div className="flex flex-wrap gap-2">
          {allTags.map((tag) => {
            const active = selectedTags.includes(tag);
            return (
              <button
                key={tag}
                type="button"
                className={`rounded-full border px-3 py-1 text-xs font-medium ${
                  active
                    ? "border-blue-400/40 bg-blue-500/15 text-blue-200"
                    : "border-white/10 bg-white/5 text-white/65 hover:bg-white/10"
                }`}
                onClick={() => toggleTag(tag)}
              >
                {tag}
              </button>
            );
          })}
        </div>
      </section>

      <section className="space-y-3">
        <div className="flex items-center justify-between gap-4">
          <h3 className="text-lg font-semibold text-white/90">Playbooks</h3>
          <span className="text-xs text-white/45">{filteredPlaybooks.length} matches</span>
        </div>
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {filteredPlaybooks.map((playbook) => (
            <div key={playbook.id} className="rounded-2xl border border-white/10 bg-white/5 p-4">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="text-sm font-semibold text-white/90">{playbook.title}</p>
                  <p className="mt-1 text-xs text-white/45">{playbook.project_id}</p>
                </div>
                <div className="flex gap-2">
                  <span className={`rounded-full border px-2 py-0.5 text-[10px] ${badgeClasses(playbook.risk_level)}`}>
                    {riskLabel(playbook.risk_level)}
                  </span>
                  <span className="rounded-full bg-white/5 px-2 py-0.5 text-[10px] text-white/60">
                    {playbook.policy_default}
                  </span>
                </div>
              </div>
              <p className="mt-3 text-sm text-white/60">{playbook.description}</p>
              <div className="mt-3 flex flex-wrap gap-2">
                {playbook.tags.map((tag) => (
                  <span key={`${playbook.id}-${tag}`} className="rounded-full bg-white/5 px-2 py-0.5 text-[10px] text-white/55">
                    {tag}
                  </span>
                ))}
              </div>
              <div className="mt-4">
                <button
                  type="button"
                  className="rounded-xl bg-blue-500/20 px-3 py-2 text-sm font-medium text-blue-200 hover:bg-blue-500/30"
                  onClick={() => {
                    if (playbook.policy_default === "AUTO") {
                      void runPlaybook(playbook.id);
                      return;
                    }
                    setConfirmTarget({
                      type: "playbook",
                      id: playbook.id,
                      label: playbook.title,
                      phrase: "RUN",
                      reason:
                        playbook.policy_default === "BREAK_GLASS"
                          ? "Break-glass playbooks require the RUN confirm phrase before execution."
                          : "Privileged playbooks require a typed confirmation before the approval flow starts.",
                    });
                    setConfirmValue("");
                  }}
                >
                  Run Playbook
                </button>
              </div>
            </div>
          ))}
        </div>
      </section>

      <section className="space-y-3">
        <div className="flex items-center justify-between gap-4">
          <h3 className="text-lg font-semibold text-white/90">Executor Actions</h3>
          <span className="text-xs text-white/45">{filteredActions.length} matches</span>
        </div>
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {filteredActions.map((action) => {
            const requiresConfirm = action.tier === "privileged_ops" || action.tier === "destructive_ops";
            const riskTone = action.tier === "destructive_ops" ? "high" : action.tier === "privileged_ops" ? "med" : "low";
            return (
              <div key={action.id} className="rounded-2xl border border-white/10 bg-white/5 p-4">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="text-sm font-semibold text-white/90">{action.title}</p>
                    <p className="mt-1 text-xs text-white/45">{action.id}</p>
                  </div>
                  <div className="flex gap-2">
                    <span className={`rounded-full border px-2 py-0.5 text-[10px] ${badgeClasses(riskTone)}`}>
                      {rawRiskLabel(action.tier)}
                    </span>
                    <span className="rounded-full bg-white/5 px-2 py-0.5 text-[10px] text-white/60">
                      {rawTierLabel(action.tier)}
                    </span>
                  </div>
                </div>
                <p className="mt-3 text-sm text-white/60">{action.description}</p>
                <div className="mt-3 flex flex-wrap gap-2">
                  {action.tags.map((tag) => (
                    <span key={`${action.id}-${tag}`} className="rounded-full bg-white/5 px-2 py-0.5 text-[10px] text-white/55">
                      {tag}
                    </span>
                  ))}
                </div>
                <div className="mt-4">
                  <button
                    type="button"
                    className="rounded-xl bg-white/10 px-3 py-2 text-sm font-medium text-white/90 hover:bg-white/15 disabled:opacity-60"
                    onClick={() => void runAction(action.id, requiresConfirm)}
                    disabled={loading !== null}
                  >
                    Run Action
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      </section>

      {confirmTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4">
          <div className="w-full max-w-md rounded-3xl border border-white/10 bg-[#0b1220] p-6 shadow-2xl">
            <p className="text-lg font-semibold text-white/95">Typed confirmation required</p>
            <p className="mt-2 text-sm text-white/60">
              {confirmTarget.reason} Type <code>{confirmTarget.phrase}</code> to continue with {confirmTarget.label}.
            </p>
            <input
              value={confirmValue}
              onChange={(event) => setConfirmValue(event.target.value)}
              className="mt-4 w-full rounded-2xl border border-white/10 bg-white/5 px-4 py-3 text-sm text-white outline-none placeholder:text-white/35 focus:border-red-400/50"
              placeholder={`Type ${confirmTarget.phrase}`}
            />
            <div className="mt-4 flex justify-end gap-2">
              <button
                type="button"
                className="rounded-xl bg-white/10 px-3 py-2 text-sm font-medium text-white/80 hover:bg-white/15"
                onClick={() => setConfirmTarget(null)}
              >
                Cancel
              </button>
              <button
                type="button"
                className="rounded-xl bg-red-500/20 px-3 py-2 text-sm font-medium text-red-200 hover:bg-red-500/30 disabled:opacity-60"
                onClick={() => void submitConfirm()}
                disabled={confirmValue !== confirmTarget.phrase}
              >
                Confirm
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default function CatalogPage() {
  return (
    <Suspense
      fallback={
        <div className="rounded-2xl border border-white/10 bg-white/5 p-6 text-sm text-white/60">
          Loading catalog…
        </div>
      }
    >
      <CatalogPageContent />
    </Suspense>
  );
}
