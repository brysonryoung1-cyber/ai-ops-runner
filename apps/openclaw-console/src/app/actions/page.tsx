"use client";

import { useState } from "react";
import ActionButton from "@/components/ActionButton";
import CollapsibleOutput from "@/components/CollapsibleOutput";
import { useExec, ExecResult } from "@/lib/hooks";

interface ActionDef {
  action: string;
  label: string;
  description: string;
  variant: "primary" | "secondary" | "danger";
}

/** Actions that mutate remote state and require confirmation before execution. */
const DESTRUCTIVE_ACTIONS = new Set(["apply", "guard"]);

const ACTIONS: ActionDef[] = [
  {
    action: "doctor",
    label: "Run Doctor",
    description: "Full health check of the OpenClaw stack on aiops-1",
    variant: "primary",
  },
  {
    action: "apply",
    label: "Apply OpenClaw (Remote)",
    description:
      "Sync repo, rebuild Docker, apply SSH fix, and verify — all in one shot",
    variant: "danger",
  },
  {
    action: "guard",
    label: "Install / Repair Guard",
    description:
      "Deploy or repair the openclaw-guard systemd timer (runs every 10 min)",
    variant: "secondary",
  },
  {
    action: "ports",
    label: "Show Port Audit",
    description: "List all listening TCP ports (ss -lntp) on aiops-1",
    variant: "secondary",
  },
];

export default function ActionsPage() {
  const { exec, loading, results } = useExec();
  const [lastAction, setLastAction] = useState<string | null>(null);

  const handleExec = async (action: string) => {
    // Require explicit confirmation for destructive/non-idempotent actions
    if (DESTRUCTIVE_ACTIONS.has(action)) {
      const confirmed = window.confirm(
        `"${action}" will modify the remote server. Are you sure you want to proceed?`
      );
      if (!confirmed) return;
    }

    setLastAction(action);
    await exec(action);
  };

  const lastResult: ExecResult | undefined = lastAction
    ? results[lastAction]
    : undefined;

  return (
    <div>
      <div className="mb-8">
        <h2 className="text-2xl font-bold text-apple-text tracking-tight">
          Actions
        </h2>
        <p className="text-sm text-apple-muted mt-1">
          Execute allowlisted operations on aiops-1 via Tailscale SSH
        </p>
      </div>

      {/* Action buttons */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {ACTIONS.map((a) => (
          <ActionButton
            key={a.action}
            label={a.label}
            description={a.description}
            variant={a.variant}
            loading={loading === a.action}
            onClick={() => handleExec(a.action)}
          />
        ))}
      </div>

      {/* Last result */}
      {lastResult && (
        <div className="mt-8">
          <div className="bg-apple-card rounded-apple border border-apple-border shadow-apple overflow-hidden">
            {/* Result header */}
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

            {/* Error message */}
            {lastResult.error && (
              <div className="px-5 py-3 bg-red-50 border-b border-red-200">
                <p className="text-xs text-red-700">{lastResult.error}</p>
              </div>
            )}

            {/* stdout */}
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

            {/* stderr (collapsible) */}
            {lastResult.stderr && (
              <div className="px-5 pb-4">
                <CollapsibleOutput
                  label="stderr"
                  output={lastResult.stderr.replace(/\x1b\[[0-9;]*m/g, "")}
                />
              </div>
            )}

            {/* Empty output */}
            {!lastResult.stdout && !lastResult.stderr && !lastResult.error && (
              <div className="px-5 py-4">
                <p className="text-xs text-apple-muted">
                  Command produced no output.
                </p>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Security note */}
      <div className="mt-8 p-4 rounded-apple bg-gray-50 border border-apple-border">
        <p className="text-xs text-apple-muted">
          <span className="font-semibold">Security:</span> Only allowlisted
          commands are executed. No arbitrary command execution. All traffic goes
          over Tailscale SSH. The console binds to 127.0.0.1 only.
        </p>
      </div>
    </div>
  );
}
