"use client";

import { useEffect, useState, useCallback } from "react";
import StatusCard from "@/components/StatusCard";
import CollapsibleOutput from "@/components/CollapsibleOutput";
import { useExec, ExecResult } from "@/lib/hooks";
import { useToken } from "@/lib/token-context";

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

  // Check connectivity on mount
  useEffect(() => {
    fetch("/api/exec?check=connectivity")
      .then((r) => r.json())
      .then((d) => {
        setConnected(d.ok);
        if (!d.ok) setConnError(d.error);
      })
      .catch(() => {
        setConnected(false);
        setConnError("Cannot reach the console API");
      });
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

  return (
    <div>
      {/* Page header */}
      <div className="mb-8">
        <h2 className="text-2xl font-bold text-apple-text tracking-tight">
          Overview
        </h2>
        <p className="text-sm text-apple-muted mt-1">
          OpenClaw HQ — System health for aiops-1 via Tailscale SSH
        </p>
      </div>

      {/* Connection banner */}
      {connected === false && (
        <div className="mb-6 p-4 rounded-apple bg-red-50 border border-red-200">
          <p className="text-sm font-semibold text-apple-red">
            SSH Connection Failed
          </p>
          <p className="text-xs text-red-600 mt-1">{connError}</p>
          <p className="text-xs text-apple-muted mt-2">
            Ensure Tailscale is running and you can reach aiops-1.
          </p>
        </div>
      )}

      {connected === null && (
        <div className="mb-6 p-4 rounded-apple bg-blue-50 border border-blue-200">
          <p className="text-sm text-apple-blue font-medium">
            Checking SSH connectivity…
          </p>
        </div>
      )}

      {/* Project Brain panel */}
      {projectState && (
        <div className="mb-6 bg-apple-card rounded-apple border border-apple-border shadow-apple overflow-hidden">
          <div className="px-5 py-3 bg-gradient-to-r from-slate-50 to-slate-100 border-b border-apple-border flex items-center justify-between">
            <span className="text-sm font-semibold text-apple-text">
              Project Brain
            </span>
            <span className="text-[10px] font-medium px-2 py-0.5 rounded-full bg-slate-200 text-slate-700">
              Canonical state
            </span>
          </div>
          <div className="p-5">
            <p className="text-sm text-apple-text mb-3">
              {projectState.goal_summary || "—"}
            </p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs">
              <div>
                <span className="text-apple-muted uppercase tracking-wider">Phase</span>
                <p className="text-apple-text font-medium mt-0.5">
                  Zane agent Phase {projectState.zane_agent_phase ?? 0}
                </p>
              </div>
              <div>
                <span className="text-apple-muted uppercase tracking-wider">Next action</span>
                <p className="text-apple-text font-medium mt-0.5">
                  {projectState.next_action_text || "—"}
                </p>
              </div>
              <div>
                <span className="text-apple-muted uppercase tracking-wider">Last deploy</span>
                <p className="text-apple-text mt-0.5">{projectState.last_deploy_timestamp || "—"}</p>
              </div>
              <div>
                <span className="text-apple-muted uppercase tracking-wider">Last guard / doctor</span>
                <p className="text-apple-text mt-0.5">
                  {projectState.last_guard_result ?? "—"} / {projectState.last_doctor_result ?? "—"}
                </p>
              </div>
              <div>
                <span className="text-apple-muted uppercase tracking-wider">LLM primary</span>
                <p className="text-apple-text mt-0.5">
                  {projectState.llm_primary_provider ?? "—"} / {projectState.llm_primary_model ?? "—"}
                </p>
              </div>
              <div>
                <span className="text-apple-muted uppercase tracking-wider">LLM fallback</span>
                <p className="text-apple-text mt-0.5">
                  {projectState.llm_fallback_provider ?? "—"} / {projectState.llm_fallback_model ?? "—"}
                </p>
              </div>
            </div>
            <div className="mt-4 pt-4 border-t border-apple-border flex flex-wrap gap-2">
              {DOC_NAMES.map(({ key, label }) => (
                <button
                  key={key}
                  type="button"
                  onClick={() => openDoc(key, label)}
                  className="px-3 py-1.5 text-xs font-medium text-apple-blue bg-blue-50 rounded-lg hover:bg-blue-100 transition-colors"
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Deploy+Verify (admin only) */}
      {isAdmin && (
        <div className="mb-6 bg-apple-card rounded-apple border border-apple-border shadow-apple overflow-hidden">
          <div className="px-5 py-3 bg-gradient-to-r from-amber-50 to-orange-50 border-b border-apple-border flex items-center justify-between flex-wrap gap-2">
            <span className="text-sm font-semibold text-apple-text">
              Deploy+Verify
            </span>
            <span className="text-[10px] font-medium px-2 py-0.5 rounded-full bg-amber-200 text-amber-800">
              Pull, build, verify, update project state
            </span>
          </div>
          <div className="p-5">
            <p className="text-xs text-apple-muted mb-3">
              Pull origin/main → rebuild → verify production → update Project Brain. Proof in artifacts/deploy/&lt;run_id&gt;/.
            </p>
            <div className="flex flex-wrap items-center gap-3">
              <button
                type="button"
                onClick={() => {
                  if (
                    window.confirm(
                      "Run Deploy+Verify on aiops-1? (Pull, build, verify, update state.)"
                    )
                  ) {
                    exec("deploy_and_verify");
                  }
                }}
                disabled={loading !== null && loading !== "deploy_and_verify"}
                className="px-4 py-2 text-sm font-medium rounded-lg bg-amber-600 text-white hover:bg-amber-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                {loading === "deploy_and_verify" ? "Running…" : "Deploy+Verify"}
              </button>
              {deployLast && (
                <div className="flex items-center gap-2 text-xs">
                  <span
                    className={`w-2.5 h-2.5 rounded-full ${
                      deployLast.overall === "PASS" ? "bg-apple-green" : "bg-apple-red"
                    }`}
                  />
                  <span className="text-apple-text font-medium">
                    Last: {deployLast.overall ?? "—"}
                  </span>
                  {deployLast.step_failed && (
                    <span className="text-apple-muted">
                      (failed at: {deployLast.step_failed})
                    </span>
                  )}
                  {deployLast.artifact_dir && (
                    <span className="text-apple-muted" title="Artifact path">
                      • {deployLast.artifact_dir}
                    </span>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Doc modal */}
      {docModal && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
          onClick={() => setDocModal(null)}
          role="dialog"
          aria-modal="true"
          aria-label={`View ${docModal.label}`}
        >
          <div
            className="bg-white rounded-apple shadow-apple max-w-2xl w-full max-h-[80vh] flex flex-col mx-4"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="px-5 py-3 border-b border-apple-border flex justify-between items-center">
              <span className="text-sm font-semibold text-apple-text">{docModal.label}</span>
              <button
                type="button"
                onClick={() => setDocModal(null)}
                className="text-apple-muted hover:text-apple-text text-lg leading-none"
              >
                ×
              </button>
            </div>
            <div className="p-5 overflow-auto flex-1">
              {docContent === null ? (
                <p className="text-sm text-apple-muted">Loading…</p>
              ) : (
                <pre className="whitespace-pre-wrap text-sm text-apple-text font-sans">
                  {docContent}
                </pre>
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
          title="SSH Bind"
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
          subtitle={portInfo?.sshBind || "Port audit for sshd binding"}
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
          <div className="bg-apple-card rounded-apple border border-apple-border shadow-apple overflow-hidden">
            <div className="px-5 py-3 bg-gray-50 border-b border-apple-border flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className={`w-2 h-2 rounded-full ${loading === "journal" ? "bg-apple-blue animate-pulse-dot" : journalResult?.ok ? "bg-apple-green" : "bg-apple-border"}`} />
                <span className="text-sm font-semibold text-apple-text">
                  Guard Log (last 20 lines)
                </span>
              </div>
              {journalResult && (
                <span className="text-xs text-apple-muted">
                  {journalResult.durationMs}ms
                </span>
              )}
            </div>
            {guardLogLines ? (
              <div className="output-block rounded-none border-0 max-h-[300px]">
                {guardLogLines}
              </div>
            ) : loading === "journal" ? (
              <div className="px-5 py-4">
                <p className="text-xs text-apple-muted">Loading guard logs…</p>
              </div>
            ) : null}
          </div>
        </div>
      )}

      {/* AI Connections Status Panel */}
      {aiStatus && (
        <div className="mt-6">
          <div className="bg-apple-card rounded-apple border border-apple-border shadow-apple overflow-hidden">
            <div className="px-5 py-3 bg-gray-50 border-b border-apple-border">
              <span className="text-sm font-semibold text-apple-text">
                AI Connections
              </span>
            </div>
            <div className="p-5">
              {/* Providers */}
              <div className="space-y-3">
                {aiStatus.providers.map((provider) => (
                  <div key={provider.name} className="flex items-center justify-between">
                    <div className="flex items-center gap-3">
                      <span
                        className={`w-2.5 h-2.5 rounded-full ${
                          provider.status === "active"
                            ? "bg-apple-green"
                            : provider.status === "inactive"
                              ? "bg-apple-red"
                              : "bg-apple-orange"
                        }`}
                      />
                      <div>
                        <p className="text-sm font-medium text-apple-text">
                          {provider.name}
                        </p>
                        <p className="text-[10px] text-apple-muted">
                          {provider.configured
                            ? `Configured · ${provider.fingerprint || "key present"}`
                            : "Not configured"}
                        </p>
                      </div>
                    </div>
                    <span
                      className={`text-[10px] font-medium px-2 py-0.5 rounded-full ${
                        provider.status === "active"
                          ? "bg-green-100 text-green-700"
                          : provider.status === "inactive"
                            ? "bg-red-100 text-red-700"
                            : "bg-gray-100 text-gray-600"
                      }`}
                    >
                      {provider.status === "active"
                        ? "Active"
                        : provider.status === "inactive"
                          ? "Inactive"
                          : "Unknown"}
                    </span>
                  </div>
                ))}
              </div>

              {/* Review Engine */}
              <div className="mt-4 pt-4 border-t border-apple-border">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-xs font-semibold text-apple-muted uppercase tracking-wider">
                      Review Engine
                    </p>
                    <p className="text-sm text-apple-text mt-0.5">
                      {aiStatus.review_engine.mode}
                    </p>
                    {aiStatus.review_engine.last_review && (
                      <p className="text-[10px] text-apple-muted mt-0.5">
                        Last review: {new Date(aiStatus.review_engine.last_review).toLocaleString()}
                      </p>
                    )}
                  </div>
                  <span className="text-[10px] font-medium px-2 py-0.5 rounded-full bg-blue-100 text-blue-700">
                    {aiStatus.review_engine.gate_status}
                  </span>
                </div>
              </div>

              {/* Security note */}
              <p className="text-[10px] text-apple-muted mt-3 pt-3 border-t border-apple-border">
                Keys are never displayed. Only masked fingerprints are shown.
              </p>
            </div>
          </div>
        </div>
      )}

      {/* LLM Providers Panel */}
      {llmStatus && (
        <div className="mt-6">
          <div className="bg-apple-card rounded-apple border border-apple-border shadow-apple overflow-hidden">
            <div className="px-5 py-3 bg-gray-50 border-b border-apple-border flex items-center justify-between">
              <span className="text-sm font-semibold text-apple-text">
                LLM Providers
              </span>
              <span className={`text-[10px] font-medium px-2 py-0.5 rounded-full ${
                llmStatus.config.valid
                  ? "bg-green-100 text-green-700"
                  : "bg-red-100 text-red-700"
              }`}>
                {llmStatus.config.valid ? "Config Valid" : "Config Error"}
              </span>
            </div>
            <div className="p-5">
              {/* Provider rows */}
              <div className="space-y-3">
                {llmStatus.providers.map((provider) => (
                  <div key={provider.name} className="flex items-center justify-between">
                    <div className="flex items-center gap-3">
                      <span
                        className={`w-2.5 h-2.5 rounded-full ${
                          provider.status === "active"
                            ? "bg-apple-green"
                            : provider.status === "disabled"
                              ? "bg-gray-300"
                              : provider.status === "inactive"
                                ? "bg-apple-red"
                                : "bg-apple-orange"
                        }`}
                      />
                      <div>
                        <p className="text-sm font-medium text-apple-text">
                          {provider.name}
                        </p>
                        <p className="text-[10px] text-apple-muted">
                          {provider.enabled
                            ? provider.configured
                              ? `Enabled · ${provider.fingerprint || "configured"}`
                              : "Enabled · Not configured"
                            : "Disabled"}
                        </p>
                      </div>
                    </div>
                    <span
                      className={`text-[10px] font-medium px-2 py-0.5 rounded-full ${
                        provider.status === "active"
                          ? "bg-green-100 text-green-700"
                          : provider.status === "disabled"
                            ? "bg-gray-100 text-gray-500"
                            : provider.status === "inactive"
                              ? "bg-red-100 text-red-700"
                              : "bg-yellow-100 text-yellow-700"
                      }`}
                    >
                      {provider.enabled ? (provider.configured ? "Active" : "No Key") : "Disabled"}
                    </span>
                  </div>
                ))}
              </div>

              {/* Router info */}
              <div className="mt-4 pt-4 border-t border-apple-border">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-xs font-semibold text-apple-muted uppercase tracking-wider">
                      Review Gate
                    </p>
                    <p className="text-sm text-apple-text mt-0.5">
                      {llmStatus.router.review_provider} · {llmStatus.router.review_model}
                    </p>
                  </div>
                  <span className="text-[10px] font-medium px-2 py-0.5 rounded-full bg-blue-100 text-blue-700">
                    {llmStatus.router.review_gate}
                  </span>
                </div>
                {llmStatus.router.review_guard === "fail" && (
                  <p className="text-xs text-amber-600 mt-2">
                    Cost guard: review model is gpt-4o without OPENCLAW_ALLOW_EXPENSIVE_REVIEW=1
                  </p>
                )}
              </div>

              {/* Provider doctor (preflight) */}
              {llmStatus.doctor && (
                <div className="mt-4 pt-4 border-t border-apple-border">
                  <p className="text-xs font-semibold text-apple-muted uppercase tracking-wider">
                    Provider Doctor
                  </p>
                  <p className="text-[10px] text-apple-muted mt-0.5">
                    Last run: {llmStatus.doctor.last_timestamp ?? "—"}
                  </p>
                  <div className="mt-2 flex flex-wrap gap-2">
                    {llmStatus.doctor.providers.openai && (
                      <span className={`text-[10px] font-medium px-2 py-0.5 rounded-full ${
                        llmStatus.doctor.providers.openai.state === "OK"
                          ? "bg-green-100 text-green-700"
                          : llmStatus.doctor.providers.openai.state === "DEGRADED"
                            ? "bg-yellow-100 text-yellow-700"
                            : "bg-red-100 text-red-700"
                      }`}>
                        OpenAI: {llmStatus.doctor.providers.openai.state}
                        {llmStatus.doctor.providers.openai.last_error_class ? ` (${llmStatus.doctor.providers.openai.last_error_class})` : ""}
                      </span>
                    )}
                    {llmStatus.doctor.providers.mistral && (
                      <span className={`text-[10px] font-medium px-2 py-0.5 rounded-full ${
                        llmStatus.doctor.providers.mistral.state === "OK"
                          ? "bg-green-100 text-green-700"
                          : llmStatus.doctor.providers.mistral.state === "DEGRADED"
                            ? "bg-yellow-100 text-yellow-700"
                            : "bg-red-100 text-red-700"
                      }`}>
                        Mistral: {llmStatus.doctor.providers.mistral.state}
                        {llmStatus.doctor.providers.mistral.last_error_class ? ` (${llmStatus.doctor.providers.mistral.last_error_class})` : ""}
                      </span>
                    )}
                  </div>
                </div>
              )}

              {/* Config error */}
              {llmStatus.config.error && (
                <div className="mt-3 p-2 rounded bg-red-50 border border-red-200">
                  <p className="text-[10px] text-red-600">
                    {llmStatus.config.error}
                  </p>
                </div>
              )}

              {/* Security note */}
              <p className="text-[10px] text-apple-muted mt-3 pt-3 border-t border-apple-border">
                Review gate always uses OpenAI Codex (fail-closed). Keys are never displayed — only masked fingerprints.
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Refresh button */}
      <div className="mt-6 flex justify-end">
        <button
          onClick={() => {
            exec("doctor");
            exec("ports");
            exec("timer");
            exec("journal");
            fetchAIStatus();
            fetchProjectState();
          }}
          disabled={!!loading}
          className="px-4 py-2 text-xs font-medium text-apple-blue bg-blue-50 rounded-lg hover:bg-blue-100 transition-colors disabled:opacity-50"
        >
          {loading ? "Refreshing…" : "Refresh All"}
        </button>
      </div>
    </div>
  );
}
