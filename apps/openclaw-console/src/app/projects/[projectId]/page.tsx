"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { useToken } from "@/lib/token-context";
import { GlassCard, StatusDot } from "@/components/glass";
import ConnectorsCard from "@/components/ConnectorsCard";
import type { ConnectorResult } from "@/components/ConnectorsCard";
import ActionButton from "@/components/ActionButton";
import { useExec } from "@/lib/hooks";
import { telemetryClick, telemetryError } from "@/lib/ui-telemetry";

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
  last_auto_finish_status?: "PASS" | "FAIL";
  last_auto_finish_run_id?: string;
}

const SOMA_PROJECT_IDS = new Set(["soma_kajabi", "soma_kajabi_library_ownership"]);
const PRED_MARKETS_PROJECT_ID = "pred_markets";

const CONNECTOR_ACTIONS = new Set([
  "soma_connectors_status",
  "soma_kajabi_bootstrap_start",
  "soma_kajabi_bootstrap_status",
  "soma_kajabi_bootstrap_finalize",
  "soma_kajabi_gmail_connect_start",
  "soma_kajabi_gmail_connect_status",
  "soma_kajabi_gmail_connect_finalize",
]);

type Toast = { type: "success" | "error"; message: string } | null;

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
  const [connectorLoading, setConnectorLoading] = useState<string | null>(null);
  const [connectorResults, setConnectorResults] = useState<Record<string, ConnectorResult>>({});
  const [toast, setToast] = useState<Toast>(null);
  const toastTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
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

  const clearToastAfter = useCallback((ms: number) => {
    if (toastTimerRef.current) clearTimeout(toastTimerRef.current);
    toastTimerRef.current = setTimeout(() => setToast(null), ms);
  }, []);

  const handleConnectorExec = useCallback(
    async (action: string) => {
      if (!projectId || project?.id !== "soma_kajabi") return;
      telemetryClick("/projects/soma_kajabi", action);
      setConnectorLoading(action);
      const CONNECTOR_TIMEOUT_MS = 35000;
      const controller = new AbortController();
      let timeoutId: ReturnType<typeof setTimeout> | undefined;
      try {
        const headers: Record<string, string> = { "Content-Type": "application/json" };
        if (token) headers["X-OpenClaw-Token"] = token;
        timeoutId = setTimeout(() => controller.abort(), CONNECTOR_TIMEOUT_MS);
        const res = await fetch(`/api/projects/${projectId}/run`, {
            method: "POST",
            headers,
            body: JSON.stringify({ action }),
            signal: controller.signal,
          });
        const data = await res.json();
        const result: ConnectorResult = {
          ok: data.ok === true,
          stdout: data.result_summary && typeof data.result_summary !== "object" ? String(data.result_summary) : undefined,
          result_summary: typeof data.result_summary === "object" ? data.result_summary : undefined,
          run_id: data.run_id,
          artifact_dir: data.artifact_dir,
          error_class: data.error_class,
          message: data.message,
          requirements_endpoint: data.requirements_endpoint,
          expected_secret_path_redacted: data.expected_secret_path_redacted,
          next_steps: data.next_steps,
        };
        setConnectorResults((prev) => ({ ...prev, [action]: result }));
        if (data.ok) {
          setToast({
            type: "success",
            message: `Done. Run: ${data.run_id ?? "—"}${data.artifact_dir ? ` · ${data.artifact_dir}` : ""}`,
          });
          clearToastAfter(5000);
        } else {
          telemetryError(
            "/projects/soma_kajabi",
            action,
            `${data.error_class ?? "Error"}: ${data.message ?? "Action failed"}`
          );
          setToast({
            type: "error",
            message: `${data.error_class ?? "Error"}: ${data.message ?? "Action failed"}${
              data.run_id ? ` · Run: ${data.run_id}` : ""
            }${data.artifact_dir ? ` · ${data.artifact_dir}` : ""}`,
          });
          clearToastAfter(8000);
        }
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        const isTimeout = err instanceof Error && err.name === "AbortError";
        telemetryError("/projects/soma_kajabi", action, msg);
        setToast({
          type: "error",
          message: isTimeout
            ? "Request timed out — View last run artifacts to debug."
            : "UI_ACTION_FAILED — check telemetry artifacts.",
        });
        clearToastAfter(8000);
        setConnectorResults((prev) => ({
          ...prev,
          [action]: {
            ok: false,
            error_class: isTimeout ? "CONNECTOR_TIMEOUT" : "UI_ACTION_FAILED",
            message: msg,
          },
        }));
      } finally {
        if (timeoutId !== undefined) clearTimeout(timeoutId);
        setConnectorLoading(null);
      }
    },
    [projectId, project?.id, token, clearToastAfter]
  );

  // Auto-load connectors status for Soma project (soma_kajabi uses run route)
  useEffect(() => {
    if (project?.id === "soma_kajabi") {
      handleConnectorExec("soma_connectors_status");
    } else if (project && SOMA_PROJECT_IDS.has(project.id)) {
      exec("soma_connectors_status");
    }
  }, [project?.id]); // eslint-disable-line react-hooks/exhaustive-deps
  const isPredMarkets = project?.id === PRED_MARKETS_PROJECT_ID;

  const handleExec = useCallback(
    async (action: string) => {
      if (project?.id === "soma_kajabi" && CONNECTOR_ACTIONS.has(action)) {
        await handleConnectorExec(action);
        return;
      }
      await exec(action);
    },
    [exec, project?.id, handleConnectorExec]
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
      {toast && (
        <div
          className={`mb-4 p-3 rounded-lg text-sm font-medium flex items-center justify-between ${
            toast.type === "success" ? "bg-emerald-500/20 text-emerald-200 border border-emerald-500/30" : "bg-red-500/20 text-red-200 border border-red-500/30"
          }`}
          role="alert"
        >
          <span>{toast.message}</span>
          <button
            type="button"
            onClick={() => setToast(null)}
            className="ml-2 opacity-80 hover:opacity-100"
            aria-label="Dismiss"
          >
            ×
          </button>
        </div>
      )}
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
            result={
              project.id === "soma_kajabi"
                ? connectorResults["soma_connectors_status"]
                : (results["soma_connectors_status"] as ConnectorResult)
            }
            loadingAction={project.id === "soma_kajabi" ? connectorLoading : execLoading}
            onExec={handleExec}
            nextStepsBootstrap={connectorResults["soma_kajabi_bootstrap_start"]?.next_steps}
            nextStepsGmail={connectorResults["soma_kajabi_gmail_connect_start"]?.next_steps}
            variant="glass"
            artifactDir={
              project.id === "soma_kajabi"
                ? connectorResults["soma_connectors_status"]?.artifact_dir
                : (results["soma_connectors_status"] as ConnectorResult)?.artifact_dir
            }
          />
          <div className="mb-6">
            <h3 className="text-lg font-semibold text-white/95 mb-3">Phase 0</h3>
            <ActionButton
              label="Auto-Finish Soma (Phase0 → Finish Plan)"
              description="Runs connectors_status → Phase0 → Finish Plan automatically. Handles Cloudflare (noVNC). Produces single summary artifact."
              variant="primary"
              loading={execLoading === "soma_kajabi_auto_finish"}
              disabled={execLoading !== null && execLoading !== "soma_kajabi_auto_finish"}
              onClick={() => handleExec("soma_kajabi_auto_finish")}
            />
            {project.last_auto_finish_status && (
              <div className="mt-2 flex items-center gap-2 text-xs">
                <span className={project.last_auto_finish_status === "PASS" ? "text-emerald-400" : "text-red-400"}>
                  Last: {project.last_auto_finish_status}
                </span>
                {project.last_auto_finish_run_id && (
                  <Link
                    href={`/artifacts?path=artifacts/soma_kajabi/auto_finish/${project.last_auto_finish_run_id}/SUMMARY.md`}
                    className="text-blue-400 hover:text-blue-300"
                  >
                    Open Summary
                  </Link>
                )}
              </div>
            )}
            <ActionButton
              label="Run Phase 0"
              description="Read-only: Kajabi snapshot + Gmail harvest (Zane McCourtney, has:attachment) + video_manifest.csv. Gmail optional (Kajabi-only mode)."
              variant="secondary"
              loading={execLoading === "soma_kajabi_phase0"}
              disabled={execLoading !== null && execLoading !== "soma_kajabi_phase0"}
              onClick={() => handleExec("soma_kajabi_phase0")}
            />
            <ActionButton
              label="Zane Finish Plan"
              description="Read-only punchlist from Phase0: PUNCHLIST.md, PUNCHLIST.csv, SUMMARY.json"
              variant="secondary"
              loading={execLoading === "soma_zane_finish_plan"}
              disabled={execLoading !== null && execLoading !== "soma_zane_finish_plan"}
              onClick={() => handleExec("soma_zane_finish_plan")}
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
