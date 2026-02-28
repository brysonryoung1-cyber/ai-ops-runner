"use client";

import { useState, useEffect, useCallback } from "react";
import Link from "next/link";
import StatusCard from "@/components/StatusCard";
import ActionButton from "@/components/ActionButton";
import CollapsibleOutput from "@/components/CollapsibleOutput";
import ConnectorsCard from "@/components/ConnectorsCard";
import ForbiddenBanner from "@/components/ForbiddenBanner";
import { useExec, ExecResult } from "@/lib/hooks";
import { useToken } from "@/lib/token-context";

type CardStatus = "pass" | "fail" | "loading" | "idle" | "warn";

/** Destructive actions that need confirmation. */
const DESTRUCTIVE_SOMA_ACTIONS = new Set(["soma_mirror"]);

interface SomaActionDef {
  action: string;
  label: string;
  description: string;
  variant: "primary" | "secondary" | "danger";
}

const SOMA_ACTIONS: SomaActionDef[] = [
  {
    action: "soma_snapshot_home",
    label: "Snapshot Home Library",
    description: "Take a full Kajabi snapshot of the Home User Library",
    variant: "primary",
  },
  {
    action: "soma_snapshot_practitioner",
    label: "Snapshot Practitioner Library",
    description: "Take a full Kajabi snapshot of the Practitioner Library",
    variant: "primary",
  },
  {
    action: "soma_harvest",
    label: "Harvest Gmail Videos",
    description: "Scan Zane's Gmail for video attachments and metadata",
    variant: "secondary",
  },
  {
    action: "soma_mirror",
    label: "Mirror Home → Practitioner",
    description:
      "Compute diff between libraries and produce mirror report + changelog",
    variant: "danger",
  },
  {
    action: "soma_status",
    label: "Soma Status",
    description: "Show latest Soma artifact runs and overall health",
    variant: "secondary",
  },
];

function GmailDeviceAuthInfo({ result, onComplete }: { result: ExecResult; onComplete: () => void }) {
  const stdout = result.stdout ?? "";
  let verificationUrl: string | null = null;
  let userCode: string | null = null;
  try {
    const lastLine = stdout.trim().split("\n").pop() || "";
    const parsed = JSON.parse(lastLine);
    verificationUrl = parsed.next_steps?.verification_url ?? parsed.verification_url ?? null;
    userCode = parsed.next_steps?.user_code ?? parsed.user_code ?? null;
  } catch {
    const urlMatch = stdout.match(/https:\/\/[^\s]+/);
    if (urlMatch) verificationUrl = urlMatch[0];
    const codeMatch = stdout.match(/code:\s*([A-Z0-9-]+)/i);
    if (codeMatch) userCode = codeMatch[1];
  }

  if (!verificationUrl && !userCode) return null;

  return (
    <div className="p-3 rounded-lg bg-blue-50 border border-blue-200 space-y-2">
      <p className="text-xs font-semibold text-blue-800">Device Authorization Required</p>
      {verificationUrl && (
        <p className="text-xs text-blue-700">
          Open:{" "}
          <a href={verificationUrl} target="_blank" rel="noopener noreferrer" className="underline font-mono break-all">
            {verificationUrl}
          </a>
        </p>
      )}
      {userCode && (
        <p className="text-xs text-blue-700">
          Enter code: <span className="font-mono font-bold text-blue-900">{userCode}</span>
        </p>
      )}
      <button
        type="button"
        onClick={onComplete}
        className="px-3 py-1.5 text-xs font-medium bg-blue-600 text-white rounded-lg hover:bg-blue-700"
      >
        I completed this — Finalize
      </button>
    </div>
  );
}

function parseSomaStatus(stdout: string): {
  lastRun: string;
  totalRuns: number;
  needsReview: number;
  lastStatus: CardStatus;
} {
  const raw = stdout.replace(/\x1b\[[0-9;]*m/g, "");

  // Try to extract structured data
  let lastRun = "—";
  let totalRuns = 0;
  let needsReview = 0;
  let lastStatus: CardStatus = "idle";

  const lastRunMatch = raw.match(/Last run:\s*(.+)/i);
  if (lastRunMatch) lastRun = lastRunMatch[1].trim();

  const totalMatch = raw.match(/Total runs:\s*(\d+)/i);
  if (totalMatch) totalRuns = parseInt(totalMatch[1], 10);

  const reviewMatch = raw.match(/needs_review:\s*(\d+)/i);
  if (reviewMatch) needsReview = parseInt(reviewMatch[1], 10);

  if (raw.includes("FAIL") || raw.includes("error")) {
    lastStatus = "fail";
  } else if (needsReview > 0) {
    lastStatus = "warn";
  } else if (totalRuns > 0) {
    lastStatus = "pass";
  }

  return { lastRun, totalRuns, needsReview, lastStatus };
}

type WizardStep = 1 | 2 | 3 | 4;

function WizardStepBadge({ step, currentStep, label }: { step: WizardStep; currentStep: WizardStep; label: string }) {
  const done = step < currentStep;
  const active = step === currentStep;
  return (
    <div className={`flex items-center gap-2 px-3 py-2 rounded-lg text-xs font-medium transition-all ${
      done ? "bg-green-500/10 text-green-400" :
      active ? "bg-blue-500/15 text-blue-300 ring-1 ring-blue-400/30" :
      "bg-white/5 text-white/40"
    }`}>
      <span className={`w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold ${
        done ? "bg-green-500/20" : active ? "bg-blue-500/20" : "bg-white/10"
      }`}>
        {done ? "✓" : step}
      </span>
      {label}
    </div>
  );
}

export default function SomaPage() {
  const { exec, loading, results, lastForbidden, dismissForbidden } = useExec();
  const token = useToken();
  const [lastAction, setLastAction] = useState<string | null>(null);
  const [connected, setConnected] = useState<boolean | null>(null);
  const [wizardStep, setWizardStep] = useState<WizardStep>(1);
  const [gmailPolling, setGmailPolling] = useState(false);
  const [gmailSecretExists, setGmailSecretExists] = useState<boolean | null>(null);
  const [phase0Baseline, setPhase0Baseline] = useState<"pass" | "fail" | "unknown">("unknown");

  // Check connectivity via server-mediated endpoint; 3s hard timeout
  useEffect(() => {
    const controller = new AbortController();
    const t = setTimeout(() => controller.abort(), 3000);
    fetch("/api/host-executor/status", { signal: controller.signal })
      .then((r) => r.json())
      .then((d) => setConnected(d.ok === true))
      .catch(() => setConnected(false))
      .finally(() => clearTimeout(t));
    return () => controller.abort();
  }, []);

  // Auto-load status and connector status
  useEffect(() => {
    if (connected === true) {
      exec("soma_status");
      exec("soma_connectors_status");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connected]);

  // Check Gmail secret status for wizard
  const fetchGmailSecret = useCallback(() => {
    const headers: Record<string, string> = {};
    if (token) headers["X-OpenClaw-Token"] = token;
    fetch("/api/connectors/gmail/secret-status", { headers })
      .then((r) => r.json())
      .then((d) => setGmailSecretExists(d.exists === true))
      .catch(() => setGmailSecretExists(null));
  }, [token]);

  useEffect(() => { fetchGmailSecret(); }, [fetchGmailSecret]);

  // Derive wizard step from connector status + phase0 results
  useEffect(() => {
    const connResult = results["soma_connectors_status"];
    if (!connResult) return;
    const stdout = connResult.stdout ?? "";
    let kajabi = false;
    let gmail = false;
    try {
      const d = JSON.parse(stdout.trim());
      kajabi = d.kajabi === "connected";
      gmail = d.gmail === "connected";
    } catch {
      // connector status parsing failed, leave as false
    }
    if (!kajabi) { setWizardStep(1); return; }
    if (!gmail && gmailSecretExists !== true) { setWizardStep(2); return; }
    if (phase0Baseline !== "pass") { setWizardStep(3); return; }
    setWizardStep(4);
  }, [results, gmailSecretExists, phase0Baseline]);

  // Check Phase0 baseline status
  useEffect(() => {
    const p0Result = results["soma_kajabi_phase0"];
    if (!p0Result) return;
    if (p0Result.ok) {
      const stdout = p0Result.stdout ?? "";
      if (stdout.includes("BASELINE_OK") || stdout.includes('"status":"PASS"')) {
        setPhase0Baseline("pass");
      } else {
        setPhase0Baseline("fail");
      }
    }
  }, [results]);

  // Gmail polling for device auth flow
  useEffect(() => {
    if (!gmailPolling) return;
    const interval = setInterval(() => {
      fetchGmailSecret();
      const headers: Record<string, string> = {};
      if (token) headers["X-OpenClaw-Token"] = token;
      fetch("/api/connectors/gmail/secret-status", { headers })
        .then((r) => r.json())
        .then((d) => {
          if (d.exists === true) {
            setGmailPolling(false);
            exec("soma_connectors_status");
          }
        })
        .catch(() => {});
    }, 5000);
    const timeout = setTimeout(() => setGmailPolling(false), 300000);
    return () => { clearInterval(interval); clearTimeout(timeout); };
  }, [gmailPolling, token, fetchGmailSecret, exec]);

  const handleExec = async (action: string) => {
    if (DESTRUCTIVE_SOMA_ACTIONS.has(action)) {
      const confirmed = window.confirm(
        `"${action}" will run a mutation analysis. Are you sure?`
      );
      if (!confirmed) return;
    }
    setLastAction(action);
    await exec(action);
  };

  const statusResult = results["soma_status"];
  const somaStatus = statusResult
    ? parseSomaStatus(statusResult.stdout)
    : null;

  const lastResult: ExecResult | undefined = lastAction
    ? results[lastAction]
    : undefined;

  return (
    <div>
      {/* Page header */}
      <div className="mb-8">
        <h2 className="text-2xl font-bold text-apple-text tracking-tight">
          Soma Kajabi Library
        </h2>
        <p className="text-sm text-apple-muted mt-1">
          Kajabi library ownership: snapshots, video harvest, and mirror
          operations
        </p>
      </div>

      {/* Forbidden banner (403 detection) */}
      {lastForbidden && (
        <ForbiddenBanner info={lastForbidden} onDismiss={dismissForbidden} />
      )}

      {/* Connection banner */}
      {connected === false && (
        <div className="mb-6 p-4 rounded-apple bg-red-50 border border-red-200">
          <p className="text-sm font-semibold text-apple-red">
            Host Executor unreachable
          </p>
          <p className="text-xs text-apple-muted mt-2">
            Ensure hostd is running on the host (127.0.0.1:8877).
          </p>
          <div className="flex gap-3 mt-2">
            <a href="/settings" className="text-xs text-blue-600 hover:underline">Copy UI debug</a>
            <a href="/settings#support-bundle" className="text-xs text-blue-600 hover:underline">Generate Support Bundle</a>
          </div>
        </div>
      )}

      {/* Setup Wizard */}
      <div data-testid="soma-setup-wizard" className="mb-8 p-5 rounded-apple bg-apple-card border border-apple-border shadow-apple">
        <h3 className="text-sm font-semibold text-apple-text mb-4">Phase 0 Setup Wizard</h3>
        <div className="flex flex-wrap gap-2 mb-4">
          <WizardStepBadge step={1} currentStep={wizardStep} label="Kajabi" />
          <WizardStepBadge step={2} currentStep={wizardStep} label="Gmail OAuth" />
          <WizardStepBadge step={3} currentStep={wizardStep} label="Run Phase 0" />
          <WizardStepBadge step={4} currentStep={wizardStep} label="Baseline Check" />
        </div>

        {wizardStep === 1 && (
          <div className="space-y-3">
            <p className="text-xs text-apple-muted">
              Connect Kajabi first. Ensure you have valid Kajabi session credentials available.
            </p>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => handleExec("soma_kajabi_bootstrap_start")}
                disabled={!!loading}
                className="px-4 py-2 text-xs font-medium bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50"
              >
                {loading === "soma_kajabi_bootstrap_start" ? "Starting…" : "Start Kajabi Bootstrap"}
              </button>
              <button
                type="button"
                onClick={() => handleExec("soma_connectors_status")}
                disabled={!!loading}
                className="px-4 py-2 text-xs font-medium bg-gray-100 rounded-lg hover:bg-gray-200 disabled:opacity-50"
              >
                Check Status
              </button>
            </div>
          </div>
        )}

        {wizardStep === 2 && (
          <div className="space-y-3">
            <p className="text-xs text-apple-muted">
              Upload <code className="bg-gray-100 px-1 rounded">gmail_client.json</code> in{" "}
              <Link href="/settings" className="text-blue-600 hover:underline">Settings → Connectors</Link>{" "}
              then start Gmail Connect below.
            </p>
            <div className="flex items-center gap-2 text-xs">
              <span className="text-apple-muted">gmail_client.json:</span>
              <span className={gmailSecretExists ? "text-green-600 font-medium" : "text-amber-600 font-medium"}>
                {gmailSecretExists === null ? "Checking…" : gmailSecretExists ? "Uploaded" : "Missing"}
              </span>
              <button type="button" onClick={fetchGmailSecret} className="text-blue-500 hover:underline text-[10px]">Refresh</button>
            </div>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => {
                  handleExec("soma_kajabi_gmail_connect_start");
                  setGmailPolling(true);
                }}
                disabled={!!loading || !gmailSecretExists}
                className="px-4 py-2 text-xs font-medium bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50"
              >
                {loading === "soma_kajabi_gmail_connect_start" ? "Starting…" : "Start Gmail Connect"}
              </button>
              {gmailPolling && (
                <span className="flex items-center gap-1 text-xs text-blue-600">
                  <span className="w-2 h-2 rounded-full bg-blue-500 animate-pulse" />
                  Polling for authorization…
                </span>
              )}
            </div>
            {results["soma_kajabi_gmail_connect_start"] && (
              <GmailDeviceAuthInfo result={results["soma_kajabi_gmail_connect_start"]} onComplete={() => {
                handleExec("soma_kajabi_gmail_connect_finalize");
                setGmailPolling(false);
              }} />
            )}
          </div>
        )}

        {wizardStep === 3 && (
          <div className="space-y-3">
            <p className="text-xs text-apple-muted">
              Both connectors are ready. Run Phase 0 discovery to snapshot Kajabi libraries and harvest Gmail videos.
            </p>
            <button
              type="button"
              onClick={() => handleExec("soma_kajabi_phase0")}
              disabled={!!loading}
              className="px-4 py-2 text-xs font-medium bg-green-600 text-white rounded-lg hover:bg-green-700 disabled:opacity-50"
            >
              {loading === "soma_kajabi_phase0" ? "Running Phase 0…" : "Run Phase 0"}
            </button>
          </div>
        )}

        {wizardStep === 4 && (
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <span className="w-3 h-3 rounded-full bg-green-500" />
              <p className="text-sm font-medium text-green-700">Phase 0 Baseline PASS</p>
            </div>
            <p className="text-xs text-apple-muted">
              Setup complete. All connectors operational and baseline verified.
            </p>
            {(() => {
              const p0 = results["soma_kajabi_phase0"];
              if (!p0) return null;
              try {
                const lastLine = (p0.stdout ?? "").trim().split("\n").pop() || "";
                const parsed = JSON.parse(lastLine);
                if (parsed.artifact_dir) {
                  return (
                    <Link
                      href={`/artifacts/${parsed.artifact_dir}`}
                      className="text-xs text-blue-600 hover:underline"
                    >
                      View Phase 0 artifacts →
                    </Link>
                  );
                }
              } catch { /* ignore */ }
              return null;
            })()}
          </div>
        )}
      </div>

      {/* Connectors card */}
      <ConnectorsCard
        result={results["soma_connectors_status"] as import("@/components/ConnectorsCard").ConnectorResult}
        loadingAction={loading}
        onExec={handleExec}
        variant="apple"
      />

      {/* Status cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-8">
        <StatusCard
          title="Last Run"
          status={
            loading === "soma_status"
              ? "loading"
              : somaStatus?.lastStatus || "idle"
          }
          subtitle={somaStatus?.lastRun || "No runs yet"}
        />
        <StatusCard
          title="Total Runs"
          status={
            loading === "soma_status"
              ? "loading"
              : somaStatus && somaStatus.totalRuns > 0
                ? "pass"
                : "idle"
          }
          subtitle={
            somaStatus ? `${somaStatus.totalRuns} run(s)` : "—"
          }
        />
        <StatusCard
          title="Needs Review"
          status={
            loading === "soma_status"
              ? "loading"
              : somaStatus && somaStatus.needsReview > 0
                ? "warn"
                : somaStatus
                  ? "pass"
                  : "idle"
          }
          subtitle={
            somaStatus
              ? somaStatus.needsReview > 0
                ? `${somaStatus.needsReview} item(s) need review`
                : "All clear"
              : "—"
          }
        />
      </div>

      {/* Primary Actions (3-button UX) */}
      <div className="mb-8">
        <h3 className="text-lg font-semibold text-apple-text mb-4">
          Quick Actions
        </h3>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <ActionButton
            label="Run Soma Now"
            description="Full orchestration: session check, Phase 0, finish plan, acceptance"
            variant="primary"
            loading={loading === "soma_run_to_done"}
            disabled={loading !== null && loading !== "soma_run_to_done"}
            onClick={() => handleExec("soma_run_to_done")}
          />
          <ActionButton
            label="Fix & Retry"
            description="Recovery chain: restart infra, doctor, then re-run orchestration"
            variant="secondary"
            loading={loading === "soma_fix_and_retry"}
            disabled={loading !== null && loading !== "soma_fix_and_retry"}
            onClick={() => handleExec("soma_fix_and_retry")}
          />
          <ActionButton
            label="Open Proof"
            description="View latest orchestration proof and acceptance artifacts"
            variant="secondary"
            loading={false}
            disabled={false}
            onClick={() => {
              const sr = results["soma_status"];
              if (sr?.stdout) {
                try {
                  const parsed = JSON.parse(sr.stdout.trim().split("\n").pop() || "{}");
                  if (parsed.artifact_dir) {
                    window.location.href = `/artifacts/${parsed.artifact_dir}`;
                    return;
                  }
                } catch { /* ignore */ }
              }
              window.location.href = "/artifacts/soma_kajabi";
            }}
          />
        </div>
      </div>

      {/* Advanced: primitive workflow actions */}
      <details className="mb-8">
        <summary className="text-sm font-medium text-apple-muted cursor-pointer hover:text-apple-text transition-colors">
          Advanced Actions
        </summary>
        <div className="mt-4 grid grid-cols-1 md:grid-cols-2 gap-3">
          {SOMA_ACTIONS.map((a) => (
            <ActionButton
              key={a.action}
              label={a.label}
              description={a.description}
              variant={a.variant}
              loading={loading === a.action}
              disabled={loading !== null && loading !== a.action}
              onClick={() => handleExec(a.action)}
            />
          ))}
        </div>
      </details>

      {/* Last result */}
      {lastResult && (
        <div className="mt-6">
          <div className="bg-apple-card rounded-apple border border-apple-border shadow-apple overflow-hidden">
            <div className="px-5 py-3 bg-gray-50 border-b border-apple-border flex items-center justify-between">
              <div className="flex items-center gap-3">
                <span
                  className={`w-2.5 h-2.5 rounded-full ${
                    lastResult.ok ? "bg-apple-green" : "bg-apple-red"
                  }`}
                />
                <span className="text-sm font-semibold text-apple-text">
                  {lastResult.action}
                </span>
                <span
                  className={`text-xs font-medium px-2 py-0.5 rounded-full ${
                    lastResult.ok
                      ? "bg-green-100 text-green-700"
                      : "bg-red-100 text-red-700"
                  }`}
                >
                  {lastResult.ok
                    ? "Success"
                    : lastResult.error
                      ? "Error"
                      : `Exit ${lastResult.exitCode}`}
                </span>
              </div>
              <span className="text-xs text-apple-muted">
                {lastResult.durationMs}ms
              </span>
            </div>

            {lastResult.error && (
              <div className="px-5 py-3 bg-red-50 border-b border-red-200">
                <p className="text-xs text-red-700">{lastResult.error}</p>
              </div>
            )}

            {lastResult.stdout && (
              <div className="px-5 pt-3 pb-1">
                <p className="text-xs font-medium text-apple-muted mb-2">
                  Output
                </p>
                <div className="output-block">
                  {lastResult.stdout.replace(/\x1b\[[0-9;]*m/g, "")}
                </div>
              </div>
            )}

            {lastResult.stderr && (
              <div className="px-5 pb-4">
                <CollapsibleOutput
                  label="stderr"
                  output={lastResult.stderr.replace(/\x1b\[[0-9;]*m/g, "")}
                />
              </div>
            )}

            {/* Artifact dir + next steps surfacing */}
            {(() => {
              try {
                const lastLine = (lastResult.stdout ?? "").trim().split("\n").pop() || "";
                const parsed = JSON.parse(lastLine);
                return (
                  <div className="px-5 pb-4 border-t border-apple-border pt-3 flex flex-wrap items-center gap-3">
                    {parsed.artifact_dir && (
                      <Link
                        href={`/artifacts/${parsed.artifact_dir}`}
                        className="text-xs text-blue-600 hover:underline"
                      >
                        View artifacts →
                      </Link>
                    )}
                    {parsed.next_steps && Array.isArray(parsed.next_steps) && parsed.next_steps.map((ns: string, i: number) => (
                      <span key={i} className="text-xs text-apple-muted">• {ns}</span>
                    ))}
                    {parsed.next_steps && typeof parsed.next_steps === "object" && !Array.isArray(parsed.next_steps) && parsed.next_steps.instruction && (
                      <span className="text-xs text-apple-muted">Next: {parsed.next_steps.instruction}</span>
                    )}
                  </div>
                );
              } catch {
                return null;
              }
            })()}
          </div>
        </div>
      )}

      {/* Refresh */}
      <div className="mt-6 flex justify-end">
        <button
          onClick={() => exec("soma_status")}
          disabled={!!loading}
          className="px-4 py-2 text-xs font-medium text-apple-blue bg-blue-50 rounded-lg hover:bg-blue-100 transition-colors disabled:opacity-50"
        >
          {loading === "soma_status" ? "Refreshing…" : "Refresh Status"}
        </button>
      </div>

      {/* Security note */}
      <div className="mt-6 p-4 rounded-apple bg-gray-50 border border-apple-border">
        <p className="text-xs text-apple-muted">
          <span className="font-semibold">Security:</span> All Soma operations
          run via allowlisted Host Executor actions. Kajabi session tokens
          and Gmail credentials are stored in /etc/ai-ops-runner/secrets/ (mode
          600, root-only). No plaintext passwords in the repo.
        </p>
      </div>
    </div>
  );
}
