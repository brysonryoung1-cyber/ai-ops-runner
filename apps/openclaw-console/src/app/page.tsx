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

export default function OverviewPage() {
  const { exec, loading, results } = useExec();
  const token = useToken();
  const [connected, setConnected] = useState<boolean | null>(null);
  const [connError, setConnError] = useState<string | null>(null);
  const [aiStatus, setAiStatus] = useState<AIStatus | null>(null);
  const [llmStatus, setLlmStatus] = useState<LLMStatus | null>(null);

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

  // Auto-refresh overview data on mount
  useEffect(() => {
    if (connected === true) {
      exec("doctor");
      exec("ports");
      exec("timer");
      exec("journal");
    }
    fetchAIStatus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connected]);

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
