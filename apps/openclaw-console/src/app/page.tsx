"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import Link from "next/link";
import StatusCard from "@/components/StatusCard";
import CollapsibleOutput from "@/components/CollapsibleOutput";
import { useExec, ExecResult } from "@/lib/hooks";
import { useToken } from "@/lib/token-context";
import {
  GlassCard,
  GlassButton,
  Pill,
  StatusDot,
} from "@/components/glass";

type CardStatus = "pass" | "fail" | "loading" | "idle" | "warn";

// ── AI Status Types ────────────────────────────────────────────

interface AIProvider {
  name: string;
  configured: boolean;
  fingerprint: string | null;
  status: "active" | "inactive" | "unknown" | "disabled";
}

interface ReviewEngine {
  mode: string;
  last_review: string | null;
  gate_status: string;
}

interface AIStatus {
  providers: AIProvider[];
  review_engine: ReviewEngine;
}

// ── LLM Status Types ────────────────────────────────────────────

interface LLMProviderStatus {
  name: string;
  enabled: boolean;
  configured: boolean;
  status: string;
  fingerprint: string | null;
  api_base?: string;
  review_model?: string;
}

interface LLMStatus {
  ok: boolean;
  providers: LLMProviderStatus[];
  router: {
    review_provider: string;
    review_model: string;
    review_gate: string;
    expensive_review_override?: boolean;
    review_guard?: "pass" | "fail";
  };
  config: {
    valid: boolean;
    path: string;
    error: string | null;
  };
  doctor?: {
    last_timestamp: string | null;
    providers: {
      openai?: { state: "OK" | "DEGRADED" | "DOWN"; last_error_class: string | null };
      mistral?: { state: "OK" | "DEGRADED" | "DOWN"; last_error_class: string | null };
    };
  };
}

// Project Brain (canonical state)
interface ProjectState {
  project_name?: string;
  goal_summary?: string;
  last_verified_vps_head?: string | null;
  last_deploy_timestamp?: string | null;
  last_guard_result?: string | null;
  last_doctor_result?: string | null;
  llm_primary_provider?: string;
  llm_primary_model?: string;
  llm_fallback_provider?: string;
  llm_fallback_model?: string;
  zane_agent_phase?: number;
  next_action_id?: string | null;
  next_action_text?: string | null;
  ui_accepted?: boolean | null;
  ui_accepted_at?: string | null;
  ui_accepted_commit?: string | null;
}

function deriveStatus(result?: ExecResult, loading?: boolean): CardStatus {
  if (loading) return "loading";
  if (!result) return "idle";
  return result.ok ? "pass" : "fail";
}

function parseDoctorSummary(stdout: string): string {
  // Try to extract the final summary line like "Doctor: 8/8 checks passed"
  const lines = stdout.split("\n");
  for (let i = lines.length - 1; i >= 0; i--) {
    const line = lines[i].trim();
    if (line.includes("checks passed") || line.includes("PASS") || line.includes("FAIL")) {
      return line.replace(/\x1b\[[0-9;]*m/g, ""); // strip ANSI
    }
  }
  return "";
}

function parsePortSummary(stdout: string): { lines: string[]; sshBind: string } {
  const raw = stdout.replace(/\x1b\[[0-9;]*m/g, "");
  const lines = raw.split("\n").filter((l) => l.trim() && l.includes(":22 "));
  const sshLine = lines.find((l) => l.includes("sshd") || l.includes(":22"));
  const sshBind = sshLine
    ? sshLine.includes("0.0.0.0")
      ? "0.0.0.0:22 (PUBLIC — DANGER)"
      : sshLine.includes("100.")
        ? "Tailscale IP only"
        : "Custom bind"
    : "No sshd found";
  return { lines, sshBind };
}

function parseTimerStatus(stdout: string): string {
  const raw = stdout.replace(/\x1b\[[0-9;]*m/g, "");
  if (raw.includes("active (waiting)")) return "Active (waiting)";
  if (raw.includes("active (running)")) return "Active (running)";
  if (raw.includes("inactive")) return "Inactive";
  if (raw.includes("could not be found")) return "Not installed";
  return "Unknown";
}

function parseDockerStatus(stdout: string): string {
  const raw = stdout.replace(/\x1b\[[0-9;]*m/g, "");
  if (raw.includes("Docker") && raw.includes("PASS")) return "Healthy";
  if (raw.includes("Docker") && raw.includes("FAIL")) return "Unhealthy";
  return "—";
}

/** Extract the last N lines from a string. */
function lastNLines(text: string, n: number): string {
  const raw = text.replace(/\x1b\[[0-9;]*m/g, "");
  const lines = raw.split("\n").filter((l) => l.trim());
  return lines.slice(-n).join("\n");
}

const DOC_NAMES: { key: string; label: string }[] = [
  { key: "OPENCLAW_GOALS", label: "Goals" },
  { key: "OPENCLAW_ROADMAP", label: "Roadmap" },
  { key: "OPENCLAW_DECISIONS", label: "Decisions" },
  { key: "OPENCLAW_CURRENT", label: "Current" },
  { key: "OPENCLAW_NEXT", label: "Next" },
];

export default function OverviewPage() {
  const { exec, loading, results } = useExec();
  const token = useToken();
  const [connected, setConnected] = useState<boolean | null>(null);
  const [connError, setConnError] = useState<string | null>(null);
  const [aiStatus, setAiStatus] = useState<AIStatus | null>(null);
  const [llmStatus, setLlmStatus] = useState<LLMStatus | null>(null);
  const [projectState, setProjectState] = useState<ProjectState | null>(null);
  const [docModal, setDocModal] = useState<{ name: string; label: string } | null>(null);
  const [docContent, setDocContent] = useState<string | null>(null);
  const [isAdmin, setIsAdmin] = useState<boolean>(false);
  const [deployLast, setDeployLast] = useState<{
    run_id: string | null;
    overall: string | null;
    step_failed: string | null;
    artifact_dir: string | null;
  } | null>(null);
  const [recentRuns, setRecentRuns] = useState<{
    run_id: string;
    project_id: string;
    action: string;
    status: "success" | "failure" | "error";
    finished_at: string;
    duration_ms: number;
  }[]>([]);
  const [costSummary, setCostSummary] = useState<{
    today_usd: number;
    mtd_usd: number;
    last_7_days_usd: number;
    top_project: { id: string; usd: number };
  } | null>(null);
  const [diagStatus, setDiagStatus] = useState<"idle" | "loading" | "done" | "error">("idle");
  const [diagLink, setDiagLink] = useState<string | null>(null);
  const [diagRunId, setDiagRunId] = useState<string | null>(null);

  // Check connectivity via server-mediated endpoint; 3s hard timeout
  const HOST_STATUS_TIMEOUT_MS = 3000;
  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), HOST_STATUS_TIMEOUT_MS);
    fetch("/api/host-executor/status", { signal: controller.signal })
      .then((r) => r.json())
      .then((d) => {
        if (cancelled) return;
        if (d.ok) {
          setConnected(true);
          setConnError(null);
          return;
        }
        setConnected(false);
        setConnError(d.message_redacted ?? d.error ?? "Host Executor unreachable");
      })
      .catch((err) => {
        if (cancelled) return;
        setConnected(false);
        setConnError(
          err.name === "AbortError"
            ? "Host Executor check timed out (3s)"
            : "Cannot reach the console API"
        );
      })
      .finally(() => clearTimeout(timeoutId));
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, []);

  // Fetch AI provider status
  const fetchAIStatus = useCallback(async () => {
    try {
      const headers: Record<string, string> = {};
      if (token) headers["X-OpenClaw-Token"] = token;
      const res = await fetch("/api/ai-status", { headers });
      const data = await res.json();
      if (data.ok) {
        setAiStatus({ providers: data.providers, review_engine: data.review_engine });
      }
    } catch {
      // Non-critical; AI status panel just won't render
    }
    // Also fetch full LLM status
    try {
      const headers: Record<string, string> = {};
      if (token) headers["X-OpenClaw-Token"] = token;
      const res = await fetch("/api/llm/status", { headers });
      const data = await res.json();
      if (data.ok) {
        setLlmStatus(data);
      }
    } catch {
      // Non-critical; LLM panel just won't render
    }
  }, [token]);

  // Fetch project state (canonical brain)
  const fetchProjectState = useCallback(async () => {
    try {
      const headers: Record<string, string> = {};
      if (token) headers["X-OpenClaw-Token"] = token;
      const res = await fetch("/api/project/state", { headers });
      const data = await res.json();
      if (data?.ok && data?.state) setProjectState(data.state);
    } catch {
      // Non-critical
    }
  }, [token]);

  // Auth context (Ship+Deploy visibility)
  const fetchAuthContext = useCallback(async () => {
    try {
      const headers: Record<string, string> = {};
      if (token) headers["X-OpenClaw-Token"] = token;
      const res = await fetch("/api/auth/context", { headers });
      const data = await res.json();
      if (data?.ok && typeof data.isAdmin === "boolean") setIsAdmin(data.isAdmin);
    } catch {
      setIsAdmin(false);
    }
  }, [token]);

  // Cost summary (today, MTD, 7d)
  const fetchCostSummary = useCallback(async () => {
    try {
      const headers: Record<string, string> = {};
      if (token) headers["X-OpenClaw-Token"] = token;
      const res = await fetch("/api/costs/summary", { headers });
      const data = await res.json();
      if (data?.ok && data.today_usd !== undefined) {
        setCostSummary({
          today_usd: data.today_usd,
          mtd_usd: data.mtd_usd,
          last_7_days_usd: data.last_7_days_usd,
          top_project: data.top_project ?? { id: "", usd: 0 },
        });
      } else {
        setCostSummary(null);
      }
    } catch {
      setCostSummary(null);
    }
  }, [token]);

  // Recent runs (last 5)
  const fetchRecentRuns = useCallback(async () => {
    try {
      const headers: Record<string, string> = {};
      if (token) headers["X-OpenClaw-Token"] = token;
      const res = await fetch("/api/runs?limit=5", { headers });
      const data = await res.json();
      if (data?.ok && Array.isArray(data.runs)) setRecentRuns(data.runs);
      else setRecentRuns([]);
    } catch {
      setRecentRuns([]);
    }
  }, [token]);

  // Last deploy result (proof bundle)
  const fetchDeployLast = useCallback(async () => {
    try {
      const headers: Record<string, string> = {};
      if (token) headers["X-OpenClaw-Token"] = token;
      const res = await fetch("/api/deploy/last", { headers });
      const data = await res.json();
      if (data?.ok && data.run_id) {
        setDeployLast({
          run_id: data.run_id,
          overall: data.overall ?? null,
          step_failed: data.step_failed ?? null,
          artifact_dir: data.artifact_dir ?? null,
        });
      } else {
        setDeployLast(null);
      }
    } catch {
      setDeployLast(null);
    }
  }, [token]);

  // Open doc modal and fetch content
  const openDoc = useCallback(
    async (key: string, label: string) => {
      setDocModal({ name: key, label });
      setDocContent(null);
      try {
        const headers: Record<string, string> = {};
        if (token) headers["X-OpenClaw-Token"] = token;
        const res = await fetch(`/api/project/docs/${key}`, { headers });
        const data = await res.json();
        setDocContent(data?.ok ? data.content : "Unable to load doc.");
      } catch {
        setDocContent("Request failed.");
      }
    },
    [token]
  );

  // Auto-refresh overview data on mount
  useEffect(() => {
    if (connected === true) {
      exec("doctor");
      exec("ports");
      exec("timer");
      exec("journal");
    }
    fetchAIStatus();
    fetchProjectState();
    fetchAuthContext();
    fetchDeployLast();
    fetchRecentRuns();
    fetchCostSummary();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connected]);

  // Refetch last deploy result when deploy_and_verify completes
  const deployResult = results["deploy_and_verify"];
  useEffect(() => {
    if (deployResult !== undefined) fetchDeployLast();
  }, [deployResult, fetchDeployLast]);

  const doctorResult = results["doctor"];
  const portsResult = results["ports"];
  const timerResult = results["timer"];
  const journalResult = results["journal"];

  const doctorSummary = doctorResult ? parseDoctorSummary(doctorResult.stdout) : "";
  const portInfo = portsResult ? parsePortSummary(portsResult.stdout) : null;
  const timerStatus = timerResult ? parseTimerStatus(timerResult.stdout) : "";
  const dockerStatus = doctorResult ? parseDockerStatus(doctorResult.stdout) : "";
  const guardLogLines = journalResult ? lastNLines(journalResult.stdout, 20) : "";

  const doctorStatus = deriveStatus(doctorResult, loading === "doctor");
  const guardPass = timerStatus.includes("Active");
  const llmOk = llmStatus?.config.valid ?? false;

  return (
    <div>
      {/* Page header */}
      <div className="mb-8">
        <h2 className="text-2xl font-bold text-white/95 tracking-tight">
          Control Center
        </h2>
        <p className="text-sm text-white/60 mt-1">
          OpenClaw HQ — System health for aiops-1 via Host Executor (localhost)
        </p>
      </div>

      {/* Connection banner */}
      {connected === false && (
        <div className="mb-6 p-4 rounded-2xl glass-surface border border-red-500/20">
          <p className="text-sm font-semibold text-red-300">Host Executor unreachable</p>
          <p className="text-xs text-red-200/80 mt-1">{connError}</p>
          <p className="text-xs text-white/50 mt-2">Ensure hostd is running on the host (127.0.0.1:8877).</p>
          <div className="flex gap-3 mt-2">
            <Link href="/settings" className="text-xs text-blue-300 hover:text-blue-200">
              Copy UI debug
            </Link>
            <Link href="/settings#support-bundle" className="text-xs text-blue-300 hover:text-blue-200">
              Generate Support Bundle
            </Link>
          </div>
        </div>
      )}

      {connected === null && (
        <div className="mb-6 p-4 rounded-2xl glass-surface border border-blue-500/20">
          <p className="text-sm text-blue-300 font-medium">Checking Host Executor connectivity…</p>
        </div>
      )}

      {/* Hero: Overall status (Doctor, Guard, LLM) */}
      {connected === true && (
        <GlassCard className="mb-6 p-6">
          <div className="flex flex-wrap items-center gap-6">
            <div className="flex items-center gap-2">
              <StatusDot variant={doctorStatus === "pass" ? "pass" : doctorStatus === "fail" ? "fail" : doctorStatus === "loading" ? "loading" : "idle"} />
              <span className="text-sm font-medium text-white/90">Doctor</span>
            </div>
            <div className="flex items-center gap-2">
              <StatusDot variant={loading === "timer" ? "loading" : guardPass ? "pass" : timerResult ? "warn" : "idle"} />
              <span className="text-sm font-medium text-white/90">Guard</span>
            </div>
            <div className="flex items-center gap-2">
              <StatusDot variant={llmOk ? "pass" : llmStatus ? "warn" : "idle"} />
              <span className="text-sm font-medium text-white/90">LLM</span>
            </div>
          </div>
        </GlassCard>
      )}

      {/* Project Brain panel */}
      {projectState && (
        <GlassCard className="mb-6">
          <div className="px-5 py-3 border-b border-white/10 flex items-center justify-between">
            <span className="text-sm font-semibold text-white/95">Project Brain</span>
            <Pill variant="info">Canonical state</Pill>
          </div>
          <div className="p-5">
            <p className="text-sm text-white/90 mb-3">{projectState.goal_summary || "—"}</p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs">
              <div>
                <span className="text-white/50 uppercase tracking-wider">Phase</span>
                <p className="text-white/90 font-medium mt-0.5">Zane agent Phase {projectState.zane_agent_phase ?? 0}</p>
              </div>
              <div>
                <span className="text-white/50 uppercase tracking-wider">Next action</span>
                <p className="text-white/90 font-medium mt-0.5">{projectState.next_action_text || "—"}</p>
              </div>
              <div>
                <span className="text-white/50 uppercase tracking-wider">Last deploy</span>
                <p className="text-white/90 mt-0.5">{projectState.last_deploy_timestamp || "—"}</p>
              </div>
              <div>
                <span className="text-white/50 uppercase tracking-wider">UI accepted</span>
                <p className="text-white/90 mt-0.5">
                  {projectState.ui_accepted === true
                    ? [projectState.ui_accepted_at, projectState.ui_accepted_commit].some(Boolean)
                      ? `Yes (${projectState.ui_accepted_at ?? "—"} @ ${projectState.ui_accepted_commit ?? "—"})`
                      : "Yes"
                    : projectState.ui_accepted === false
                      ? "No"
                      : "—"}
                </p>
              </div>
              <div>
                <span className="text-white/50 uppercase tracking-wider">Last guard / doctor</span>
                <p className="text-white/90 mt-0.5">
                  {projectState.last_guard_result ?? "—"} / {projectState.last_doctor_result ?? "—"}
                </p>
              </div>
              <div>
                <span className="text-white/50 uppercase tracking-wider">LLM primary</span>
                <p className="text-white/90 mt-0.5">
                  {projectState.llm_primary_provider ?? "—"} / {projectState.llm_primary_model ?? "—"}
                </p>
              </div>
              <div>
                <span className="text-white/50 uppercase tracking-wider">LLM fallback</span>
                <p className="text-white/90 mt-0.5">
                  {projectState.llm_fallback_provider ?? "—"} / {projectState.llm_fallback_model ?? "—"}
                </p>
              </div>
            </div>
            <div className="mt-4 pt-4 border-t border-white/10 flex flex-wrap gap-2">
              {DOC_NAMES.map(({ key, label }) => (
                <GlassButton
                  key={key}
                  variant="secondary"
                  size="sm"
                  onClick={() => openDoc(key, label)}
                >
                  {label}
                </GlassButton>
              ))}
            </div>
          </div>
        </GlassCard>
      )}

      {/* Deploy+Verify (admin only) */}
      {isAdmin && (
        <GlassCard className="mb-6">
          <div className="px-5 py-3 border-b border-white/10 flex items-center justify-between flex-wrap gap-2">
            <span className="text-sm font-semibold text-white/95">Quick Actions</span>
            <Pill variant="warn">Admin only</Pill>
          </div>
          <div className="p-5">
            <p className="text-xs text-white/60 mb-3">
              Pull origin/main → rebuild → verify production → update Project Brain. Proof in artifacts/deploy/&lt;run_id&gt;/.
            </p>
            <div className="flex flex-wrap items-center gap-3">
              <GlassButton
                variant="primary"
                onClick={() => {
                  if (window.confirm("Run Deploy+Verify on aiops-1? (Pull, build, verify, update state.)")) {
                    exec("deploy_and_verify");
                  }
                }}
                disabled={loading !== null && loading !== "deploy_and_verify"}
              >
                {loading === "deploy_and_verify" ? "Running…" : "Deploy+Verify"}
              </GlassButton>
              {deployLast && (
                <div className="flex items-center gap-2 text-xs">
                  <StatusDot variant={deployLast.overall === "PASS" ? "pass" : "fail"} />
                  <span className="text-white/90 font-medium">Last: {deployLast.overall ?? "—"}</span>
                  {deployLast.step_failed && (
                    <span className="text-white/50">(failed at: {deployLast.step_failed})</span>
                  )}
                  {deployLast.artifact_dir && (
                    <span className="text-white/50" title="Artifact path">• {deployLast.artifact_dir}</span>
                  )}
                </div>
              )}
            </div>
          </div>
        </GlassCard>
      )}

      {/* Recent Runs */}
      {recentRuns.length > 0 && (
        <GlassCard className="mb-6">
          <div className="px-5 py-3 border-b border-white/10 flex items-center justify-between">
            <span className="text-sm font-semibold text-white/95">Recent Runs</span>
            <Link href="/runs" className="text-xs font-medium text-blue-300 hover:text-blue-200 transition-colors">
              View all
            </Link>
          </div>
          <ul className="divide-y divide-white/5">
            {recentRuns.map((run) => (
              <li key={run.run_id}>
                <Link
                  href="/runs"
                  className="flex items-center justify-between px-5 py-3 hover:bg-white/5 transition-colors"
                >
                  <div className="flex items-center gap-3 min-w-0">
                    <StatusDot variant={run.status === "success" ? "pass" : run.status === "failure" ? "fail" : "warn"} />
                    <div>
                      <span className="text-sm font-medium text-white/90">{run.action}</span>
                      <span className="text-[10px] text-white/50 ml-2">{run.project_id}</span>
                    </div>
                  </div>
                  <Pill variant={run.status === "success" ? "success" : run.status === "failure" ? "fail" : "warn"}>
                    {run.status}
                  </Pill>
                </Link>
              </li>
            ))}
          </ul>
        </GlassCard>
      )}

      {/* Doc modal */}
      {docModal && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
          onClick={() => setDocModal(null)}
          role="dialog"
          aria-modal="true"
          aria-label={`View ${docModal.label}`}
        >
          <div
            className="glass-surface rounded-2xl max-w-2xl w-full max-h-[80vh] flex flex-col mx-4"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="px-5 py-3 border-b border-white/10 flex justify-between items-center">
              <span className="text-sm font-semibold text-white/95">{docModal.label}</span>
              <button
                type="button"
                onClick={() => setDocModal(null)}
                className="text-white/50 hover:text-white text-lg leading-none"
              >
                ×
              </button>
            </div>
            <div className="p-5 overflow-auto flex-1">
              {docContent === null ? (
                <p className="text-sm text-white/60">Loading…</p>
              ) : (
                <pre className="whitespace-pre-wrap text-sm text-white/90 font-sans">{docContent}</pre>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Status cards grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <StatusCard
          title="Doctor Status"
          status={deriveStatus(doctorResult, loading === "doctor")}
          subtitle={doctorSummary || "Health check for the OpenClaw stack"}
        >
          {doctorResult && (
            <CollapsibleOutput
              output={doctorResult.stdout.replace(/\x1b\[[0-9;]*m/g, "") + (doctorResult.stderr ? "\n" + doctorResult.stderr : "")}
              label="Full doctor output"
            />
          )}
        </StatusCard>

        <StatusCard
          title="Port audit"
          status={
            loading === "ports"
              ? "loading"
              : !portsResult
                ? "idle"
                : portInfo?.sshBind.includes("Tailscale")
                  ? "pass"
                  : portInfo?.sshBind.includes("PUBLIC")
                    ? "fail"
                    : "warn"
          }
          subtitle={portInfo?.sshBind || "Listening ports (sshd binding check)"}
        >
          {portsResult && (
            <CollapsibleOutput
              output={portsResult.stdout.replace(/\x1b\[[0-9;]*m/g, "")}
              label="Full port listing"
            />
          )}
        </StatusCard>

        <StatusCard
          title="Guard Timer"
          status={
            loading === "timer"
              ? "loading"
              : !timerResult
                ? "idle"
                : timerStatus.includes("Active")
                  ? "pass"
                  : timerStatus.includes("Not installed")
                    ? "fail"
                    : "warn"
          }
          subtitle={timerStatus || "openclaw-guard.timer systemd unit"}
        >
          {timerResult && (
            <CollapsibleOutput
              output={timerResult.stdout.replace(/\x1b\[[0-9;]*m/g, "") + (timerResult.stderr ? "\n" + timerResult.stderr : "")}
              label="Timer details"
            />
          )}
        </StatusCard>

        <StatusCard
          title="Docker Stack"
          status={
            loading === "doctor"
              ? "loading"
              : !doctorResult
                ? "idle"
                : dockerStatus === "Healthy"
                  ? "pass"
                  : dockerStatus === "Unhealthy"
                    ? "fail"
                    : "idle"
          }
          subtitle={dockerStatus || "Docker compose services"}
        />
      </div>

      {/* Guard log lines (last 20) */}
      {(guardLogLines || loading === "journal") && (
        <div className="mt-6">
          <GlassCard>
            <div className="px-5 py-3 border-b border-white/10 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <StatusDot variant={loading === "journal" ? "loading" : journalResult?.ok ? "pass" : "idle"} />
                <span className="text-sm font-semibold text-white/95">Guard Log (last 20 lines)</span>
              </div>
              {journalResult && <span className="text-xs text-white/50">{journalResult.durationMs}ms</span>}
            </div>
            {guardLogLines ? (
              <div className="output-block rounded-none border-0 max-h-[300px]">
                {guardLogLines}
              </div>
            ) : loading === "journal" ? (
              <div className="px-5 py-4">
                <p className="text-xs text-white/50">Loading guard logs…</p>
              </div>
            ) : null}
          </GlassCard>
        </div>
      )}

      {/* Cost summary tile */}
      <div className="mt-6">
        <GlassCard>
          <div className="px-5 py-3 border-b border-white/10 flex items-center justify-between">
            <span className="text-sm font-semibold text-white/95">LLM Cost</span>
            {costSummary && (
              <span className="text-xs text-white/50">Today · MTD · 7d</span>
            )}
          </div>
          <div className="p-5">
            {costSummary ? (
              <div className="space-y-3">
                {(costSummary as { guard_tripped?: boolean }).guard_tripped && (
                  <p className="text-amber-400 text-xs font-medium">Cost guard tripped — only doctor/deploy/DoD and Soma Phase0 discovery allowed.</p>
                )}
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
                  <div>
                    <p className="text-white/50 text-xs">Today</p>
                    <p className="text-white/95 font-medium">${costSummary.today_usd.toFixed(2)}</p>
                  </div>
                  <div>
                    <p className="text-white/50 text-xs">MTD</p>
                    <p className="text-white/95 font-medium">${costSummary.mtd_usd.toFixed(2)}</p>
                  </div>
                  <div>
                    <p className="text-white/50 text-xs">Last 7d</p>
                    <p className="text-white/95 font-medium">${costSummary.last_7_days_usd.toFixed(2)}</p>
                  </div>
                  <div>
                    <p className="text-white/50 text-xs">Top project</p>
                    <p className="text-white/95 font-medium truncate" title={costSummary.top_project.id}>
                      {costSummary.top_project.id || "—"} ${costSummary.top_project.usd.toFixed(2)}
                    </p>
                  </div>
                </div>
              </div>
            ) : (
              <p className="text-xs text-white/50">No usage data yet. Costs from artifacts/cost/usage.jsonl.</p>
            )}
          </div>
        </GlassCard>
      </div>

      {/* AI Connections Status Panel */}
      {aiStatus && (
        <div className="mt-6">
          <GlassCard>
            <div className="px-5 py-3 border-b border-white/10">
              <span className="text-sm font-semibold text-white/95">AI Connections</span>
            </div>
            <div className="p-5">
              <div className="space-y-3">
                {aiStatus.providers.map((provider) => (
                  <div key={provider.name} className="flex items-center justify-between">
                    <div className="flex items-center gap-3">
                      <StatusDot variant={provider.status === "active" ? "pass" : provider.status === "inactive" ? "fail" : "warn"} />
                      <div>
                        <p className="text-sm font-medium text-white/90">{provider.name}</p>
                        <p className="text-[10px] text-white/50">
                          {provider.configured ? `Configured · ${provider.fingerprint || "key present"}` : "Not configured"}
                        </p>
                      </div>
                    </div>
                    <Pill variant={provider.status === "active" ? "success" : provider.status === "inactive" ? "fail" : "warn"}>
                      {provider.status === "active" ? "Active" : provider.status === "inactive" ? "Inactive" : "Unknown"}
                    </Pill>
                  </div>
                ))}
              </div>

              <div className="mt-4 pt-4 border-t border-white/10">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-xs font-semibold text-white/50 uppercase tracking-wider">Review Engine</p>
                    <p className="text-sm text-white/90 mt-0.5">{aiStatus.review_engine.mode}</p>
                    {aiStatus.review_engine.last_review && (
                      <p className="text-[10px] text-white/50 mt-0.5">
                        Last review: {new Date(aiStatus.review_engine.last_review).toLocaleString()}
                      </p>
                    )}
                  </div>
                  <Pill variant="info">{aiStatus.review_engine.gate_status}</Pill>
                </div>
              </div>

              <p className="text-[10px] text-white/50 mt-3 pt-3 border-t border-white/10">
                Keys are never displayed. Only masked fingerprints are shown.
              </p>
            </div>
          </GlassCard>
        </div>
      )}

      {/* LLM Providers Panel */}
      {llmStatus && (
        <div className="mt-6">
          <GlassCard>
            <div className="px-5 py-3 border-b border-white/10 flex items-center justify-between">
              <span className="text-sm font-semibold text-white/95">LLM Providers</span>
              <Pill variant={llmStatus.config.valid ? "success" : "fail"}>
                {llmStatus.config.valid ? "Config Valid" : "Config Error"}
              </Pill>
            </div>
            <div className="p-5">
              <div className="space-y-3">
                {llmStatus.providers.map((provider) => (
                  <div key={provider.name} className="flex items-center justify-between">
                    <div className="flex items-center gap-3">
                      <StatusDot
                        variant={
                          provider.status === "active" ? "pass" : provider.status === "disabled" ? "idle" : provider.status === "inactive" ? "fail" : "warn"
                        }
                      />
                      <div>
                        <p className="text-sm font-medium text-white/90">{provider.name}</p>
                        <p className="text-[10px] text-white/50">
                          {provider.enabled
                            ? provider.configured
                              ? `Enabled · ${provider.fingerprint || "configured"}`
                              : "Enabled · Not configured"
                            : "Disabled"}
                        </p>
                      </div>
                    </div>
                    <Pill
                      variant={
                        provider.status === "active" ? "success" : provider.status === "disabled" ? "default" : provider.status === "inactive" ? "fail" : "warn"
                      }
                    >
                      {provider.enabled ? (provider.configured ? "Active" : "No Key") : "Disabled"}
                    </Pill>
                  </div>
                ))}
              </div>

              <div className="mt-4 pt-4 border-t border-white/10">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-xs font-semibold text-white/50 uppercase tracking-wider">Review Gate</p>
                    <p className="text-sm text-white/90 mt-0.5">
                      {llmStatus.router.review_provider} · {llmStatus.router.review_model}
                    </p>
                  </div>
                  <Pill variant="info">{llmStatus.router.review_gate}</Pill>
                </div>
                {llmStatus.router.review_guard === "fail" && (
                  <p className="text-xs text-amber-300 mt-2">
                    Cost guard: review model is gpt-4o without OPENCLAW_ALLOW_EXPENSIVE_REVIEW=1
                  </p>
                )}
              </div>

              {llmStatus.doctor && (
                <div className="mt-4 pt-4 border-t border-white/10">
                  <p className="text-xs font-semibold text-white/50 uppercase tracking-wider">
                    Provider Doctor
                  </p>
                  <p className="text-[10px] text-white/50 mt-0.5">
                    Last run: {llmStatus.doctor.last_timestamp ?? "—"}
                  </p>
                  <div className="mt-2 flex flex-wrap gap-2">
                    {llmStatus.doctor.providers.openai && (
                      <Pill
                        variant={
                          llmStatus.doctor.providers.openai.state === "OK"
                            ? "success"
                            : llmStatus.doctor.providers.openai.state === "DEGRADED"
                              ? "warn"
                              : "fail"
                        }
                      >
                        OpenAI: {llmStatus.doctor.providers.openai.state}
                        {llmStatus.doctor.providers.openai.last_error_class ? ` (${llmStatus.doctor.providers.openai.last_error_class})` : ""}
                      </Pill>
                    )}
                    {llmStatus.doctor.providers.mistral && (
                      <Pill
                        variant={
                          llmStatus.doctor.providers.mistral.state === "OK"
                            ? "success"
                            : llmStatus.doctor.providers.mistral.state === "DEGRADED"
                              ? "warn"
                              : "fail"
                        }
                      >
                        Mistral: {llmStatus.doctor.providers.mistral.state}
                        {llmStatus.doctor.providers.mistral.last_error_class ? ` (${llmStatus.doctor.providers.mistral.last_error_class})` : ""}
                      </Pill>
                    )}
                  </div>
                </div>
              )}

              {llmStatus.config.error && (
                <div className="mt-3 p-2 rounded-lg bg-red-500/10 border border-red-500/20">
                  <p className="text-[10px] text-red-300">{llmStatus.config.error}</p>
                </div>
              )}

              <p className="text-[10px] text-white/50 mt-3 pt-3 border-t border-white/10">
                Review gate always uses OpenAI Codex (fail-closed). Keys are never displayed — only masked fingerprints.
              </p>
            </div>
          </GlassCard>
        </div>
      )}

      {/* Collect Diagnostics */}
      <div className="mt-6">
        <GlassCard>
          <div className="px-5 py-3 border-b border-white/10 flex items-center justify-between">
            <span className="text-sm font-semibold text-white/95">Diagnostics</span>
          </div>
          <div className="p-5 flex items-center gap-3 flex-wrap">
            <GlassButton
              onClick={async () => {
                setDiagStatus("loading");
                try {
                  const headers: Record<string, string> = {};
                  if (token) headers["X-OpenClaw-Token"] = token;
                  const res = await fetch("/api/support/bundle", { method: "POST", headers });
                  const data = await res.json();
                  if (data.ok && data.permalink) {
                    setDiagLink(data.permalink);
                    setDiagRunId(data.run_id ?? null);
                    setDiagStatus("done");
                  } else {
                    setDiagStatus("error");
                  }
                } catch {
                  setDiagStatus("error");
                }
              }}
              disabled={diagStatus === "loading"}
            >
              {diagStatus === "idle" && "Collect Diagnostics"}
              {diagStatus === "loading" && "Collecting…"}
              {diagStatus === "done" && "Done"}
              {diagStatus === "error" && "Failed — retry"}
            </GlassButton>
            {diagLink && (
              <Link href={diagLink} className="text-xs text-blue-300 hover:text-blue-200">
                View bundle →
              </Link>
            )}
            {diagRunId && (
              <span className="text-[10px] text-white/40 font-mono">run_id: {diagRunId}</span>
            )}
          </div>
        </GlassCard>
      </div>

      <div className="mt-6 flex justify-end">
        <GlassButton
          onClick={() => {
            exec("doctor");
            exec("ports");
            exec("timer");
            exec("journal");
            fetchAIStatus();
            fetchProjectState();
            fetchRecentRuns();
          }}
          disabled={!!loading}
        >
          {loading ? "Refreshing…" : "Refresh All"}
        </GlassButton>
      </div>
    </div>
  );
}
