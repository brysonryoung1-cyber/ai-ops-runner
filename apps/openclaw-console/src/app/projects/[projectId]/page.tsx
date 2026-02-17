"use client";

import { useEffect, useState, useCallback } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { useToken } from "@/lib/token-context";
import { GlassCard, StatusDot } from "@/components/glass";
import ConnectorsCard from "@/components/ConnectorsCard";
import ActionButton from "@/components/ActionButton";
import { useExec } from "@/lib/hooks";

interface LastRun {
  run_id: string;
  action: string;
  status: "success" | "failure" | "error";
  finished_at: string;
  duration_ms: number;
  error_summary: string | null;
}

interface ProjectData {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
  workflows: string[];
  schedules: { workflow: string; cron: string; label: string }[];
  notification_flags: {
    on_success: boolean;
    on_failure: boolean;
    on_recovery: boolean;
    channels: string[];
  };
  tags: string[];
  last_run: LastRun | null;
}

const SOMA_PROJECT_IDS = new Set(["soma_kajabi", "soma_kajabi_library_ownership"]);
const PRED_MARKETS_PROJECT_ID = "pred_markets";

function statusColor(project: ProjectData): { dot: "pass" | "fail" | "warn" | "idle"; label: string; labelColor: string } {
  if (!project.enabled) return { dot: "idle", label: "Disabled", labelColor: "text-white/40" };
  if (!project.last_run) return { dot: "warn", label: "No runs", labelColor: "text-amber-400" };
  if (project.last_run.status === "success") return { dot: "pass", label: "Healthy", labelColor: "text-emerald-400" };
  return { dot: "fail", label: project.last_run.status === "error" ? "Error" : "Failing", labelColor: "text-red-400" };
}

function formatRelativeTime(isoString: string): string {
  const diff = Date.now() - new Date(isoString).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

export default function ProjectDetailsPage() {
  const params = useParams();
  const projectId = typeof params.projectId === "string" ? params.projectId : "";
  const token = useToken();
  const [project, setProject] = useState<ProjectData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const { exec, loading: execLoading, results } = useExec();

  const fetchProject = useCallback(async () => {
    if (!projectId) return;
    setLoading(true);
    try {
      const headers: Record<string, string> = {};
      if (token) headers["X-OpenClaw-Token"] = token;
      const res = await fetch("/api/projects", { headers });
      const data = await res.json();
      if (data.ok && Array.isArray(data.projects)) {
        const found = data.projects.find((p: ProjectData) => p.id === projectId);
        if (found) {
          setProject(found);
          setError(null);
        } else {
          setError("Project not found");
          setProject(null);
        }
      } else {
        setError(data.error || "Failed to load project");
        setProject(null);
      }
    } catch (err) {
      setError(`Network error: ${err instanceof Error ? err.message : String(err)}`);
      setProject(null);
    } finally {
      setLoading(false);
    }
  }, [projectId, token]);

  useEffect(() => {
    fetchProject();
  }, [fetchProject]);

  // Auto-load connectors status for Soma projects
  useEffect(() => {
    if (project && SOMA_PROJECT_IDS.has(project.id)) {
      exec("soma_connectors_status");
    }
  }, [project?.id]); // eslint-disable-line react-hooks/exhaustive-deps
  const isPredMarkets = project?.id === PRED_MARKETS_PROJECT_ID;

  const handleExec = useCallback(
    async (action: string) => {
      await exec(action);
    },
    [exec]
  );

  const isSoma = project ? SOMA_PROJECT_IDS.has(project.id) : false;
  const status = project ? statusColor(project) : null;

  if (!projectId) {
    return (
      <div className="mb-6 p-4 rounded-2xl glass-surface border border-red-500/20">
        <p className="text-sm font-semibold text-red-300">Invalid project</p>
        <p className="text-xs text-red-200/80 mt-1">Missing project ID.</p>
        <Link href="/projects" className="text-xs text-blue-400 hover:text-blue-300 mt-2 inline-block">
          ← Back to Projects
        </Link>
      </div>
    );
  }

  if (loading && !project) {
    return (
      <div className="space-y-4">
        <div className="h-8 w-48 rounded-lg bg-white/10 animate-pulse" />
        <div className="h-32 rounded-2xl glass-surface animate-pulse" />
        <Link href="/projects" className="text-xs text-blue-400 hover:text-blue-300">
          ← Back to Projects
        </Link>
      </div>
    );
  }

  if (error || !project) {
    return (
      <div className="mb-6 p-4 rounded-2xl glass-surface border border-red-500/20">
        <p className="text-sm font-semibold text-red-300">Error</p>
        <p className="text-xs text-red-200/80 mt-1">{error || "Project not found"}</p>
        <Link href="/projects" className="text-xs text-blue-400 hover:text-blue-300 mt-2 inline-block">
          ← Back to Projects
        </Link>
      </div>
    );
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <Link href="/projects" className="text-xs text-blue-400 hover:text-blue-300 mb-2 inline-block">
            ← Projects
          </Link>
          <h2 className="text-2xl font-bold text-white/95 tracking-tight">{project.name}</h2>
          <p className="text-sm text-white/60 mt-1">{project.description}</p>
        </div>
        <div className="flex items-center gap-2">
          {status && (
            <>
              <StatusDot variant={status.dot} />
              <span className={`text-sm font-medium ${status.labelColor}`}>{status.label}</span>
            </>
          )}
        </div>
      </div>

      <GlassCard className="mb-6 p-5">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <p className="text-[10px] font-semibold text-white/50 uppercase tracking-wider mb-0.5">Last Run</p>
            {project.last_run ? (
              <div>
                <p className="text-sm text-white/90 font-medium">{formatRelativeTime(project.last_run.finished_at)}</p>
                <p className={`text-xs ${project.last_run.status === "success" ? "text-emerald-400" : "text-red-400"}`}>
                  {project.last_run.action} — {project.last_run.status}
                </p>
              </div>
            ) : (
              <p className="text-sm text-white/50">No runs yet</p>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <Link
              href={`/runs?project=${encodeURIComponent(project.id)}`}
              className="text-sm font-medium text-blue-400 hover:text-blue-300"
            >
              Runs
            </Link>
            <span className="text-white/40">·</span>
            <Link href="/artifacts" className="text-sm font-medium text-blue-400 hover:text-blue-300">
              Artifacts
            </Link>
          </div>
        </div>
      </GlassCard>

      {isSoma && (
        <>
          <ConnectorsCard
            result={results["soma_connectors_status"]}
            loading={execLoading === "soma_connectors_status"}
            onExec={handleExec}
            variant="glass"
          />
          <div className="mb-6">
            <h3 className="text-lg font-semibold text-white/95 mb-3">Phase 0</h3>
            <ActionButton
              label="Run Phase 0"
              description="Read-only: Kajabi snapshot + Gmail harvest (Zane McCourtney, has:attachment) + video_manifest.csv"
              variant="primary"
              loading={execLoading === "soma_kajabi_phase0"}
              disabled={execLoading !== null && execLoading !== "soma_kajabi_phase0"}
              onClick={() => handleExec("soma_kajabi_phase0")}
            />
          </div>
        </>
      )}

      {isPredMarkets && (
        <div className="mb-6">
          <h3 className="text-lg font-semibold text-white/95 mb-3">Phase 0 Mirror</h3>
          <p className="text-sm text-white/60 mb-3">
            Kill switch and phase are read-only (display only). Run mirror or health report below.
          </p>
          <div className="flex flex-wrap gap-3">
            <ActionButton
              label="Run Mirror (Phase 0)"
              description="Snapshot Kalshi + Polymarket public markets into artifacts"
              variant="primary"
              loading={execLoading === "pred_markets.mirror.run"}
              disabled={execLoading !== null && execLoading !== "pred_markets.mirror.run"}
              onClick={() => handleExec("pred_markets.mirror.run")}
            />
            <ActionButton
              label="Run Health Report"
              description="Check config + connector reachability"
              variant="secondary"
              loading={execLoading === "pred_markets.report.health"}
              disabled={execLoading !== null && execLoading !== "pred_markets.report.health"}
              onClick={() => handleExec("pred_markets.report.health")}
            />
          </div>
        </div>
      )}
    </div>
  );
}
