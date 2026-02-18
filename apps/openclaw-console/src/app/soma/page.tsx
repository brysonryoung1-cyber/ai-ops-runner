"use client";

import { useState, useEffect } from "react";
import StatusCard from "@/components/StatusCard";
import ActionButton from "@/components/ActionButton";
import CollapsibleOutput from "@/components/CollapsibleOutput";
import ConnectorsCard from "@/components/ConnectorsCard";
import ForbiddenBanner from "@/components/ForbiddenBanner";
import { useExec, ExecResult } from "@/lib/hooks";

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

export default function SomaPage() {
  const { exec, loading, results, lastForbidden, dismissForbidden } = useExec();
  const [lastAction, setLastAction] = useState<string | null>(null);
  const [connected, setConnected] = useState<boolean | null>(null);

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

      {/* Action buttons */}
      <div className="mb-8">
        <h3 className="text-lg font-semibold text-apple-text mb-4">
          Workflow Actions
        </h3>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
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
      </div>

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
