"use client";

import { useState, useEffect, type ReactNode } from "react";
import ActionButton from "@/components/ActionButton";

/** Normalize unknown values to safe ReactNode. Never render raw secrets. */
function toSafeReactNode(value: unknown): ReactNode {
  if (value == null) return null;
  if (typeof value === "string" || typeof value === "number") return String(value);
  if (value instanceof Error) return value.message;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}
import CollapsibleOutput from "@/components/CollapsibleOutput";
import { useExec, ExecResult } from "@/lib/hooks";
import { GlassCard, StatusDot, Pill } from "@/components/glass";

interface ActionDef {
  action: string;
  label: string;
  description: string;
  variant: "primary" | "secondary" | "danger";
}

/** Actions that mutate remote state and require confirmation before execution. */
const DESTRUCTIVE_ACTIONS = new Set(["apply", "guard", "soma_mirror"]);

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
      "Sync repo, rebuild Docker, and verify — all in one shot (run from ship host, not VPS)",
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
  {
    action: "journal",
    label: "Tail Guard Log",
    description:
      "Show the last 200 lines of the openclaw-guard service journal",
    variant: "secondary",
  },
  {
    action: "soma_kajabi_phase0",
    label: "Soma Kajabi Phase 0",
    description:
      "Read-only: Kajabi snapshot + Gmail harvest (Zane McCourtney, has:attachment) + video_manifest.csv",
    variant: "primary",
  },
  {
    action: "soma_kajabi_auto_finish",
    label: "Auto-Finish Soma (Phase0 → Finish Plan)",
    description:
      "Runs connectors_status → Phase0 → Finish Plan automatically. Handles Cloudflare (noVNC). Produces single summary artifact.",
    variant: "primary",
  },
];

const ORB_BACKTEST_ACTIONS: ActionDef[] = [
  {
    action: "orb.backtest.bulk",
    label: "ORB Tier-1 Bulk Backtest",
    description: "Tier-1 bulk backtest. Locked until Soma Phase 0 baseline PASS.",
    variant: "secondary",
  },
  {
    action: "orb.backtest.confirm_nt8",
    label: "ORB Tier-2 Confirm NT8",
    description: "Tier-2 confirmation stub. Locked until Soma Phase 0 baseline PASS.",
    variant: "secondary",
  },
];

interface GateState {
  allow_orb_backtests: boolean;
  phase0_baseline_artifact_dir: string | null;
}

export default function ActionsPage() {
  const { exec, loading, results } = useExec();
  const [lastAction, setLastAction] = useState<string | null>(null);
  const [gateState, setGateState] = useState<GateState | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/project/state")
      .then((r) => r.json())
      .then((data) => {
        if (cancelled || !data?.state) return;
        const st = data.state as Record<string, unknown>;
        const gates = (st.gates as Record<string, unknown>) ?? {};
        const projects = (st.projects as Record<string, unknown>) ?? {};
        const sk = (projects.soma_kajabi as Record<string, unknown>) ?? {};
        setGateState({
          allow_orb_backtests: gates.allow_orb_backtests === true,
          phase0_baseline_artifact_dir: (sk.phase0_baseline_artifact_dir as string) || null,
        });
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

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
        <h2 className="text-2xl font-bold text-white/95 tracking-tight">Actions</h2>
        <p className="text-sm text-white/60 mt-1">Execute allowlisted operations via Host Executor (localhost)</p>
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
            disabled={loading !== null && loading !== a.action}
            onClick={() => handleExec(a.action)}
          />
        ))}
      </div>

      {/* ORB Backtest — Soma-first gate lock banner */}
      <div className="mt-8">
        <h3 className="text-lg font-semibold text-white/90 mb-2">ORB Backtest</h3>
        {gateState && !gateState.allow_orb_backtests && (
          <div className="mb-4 p-4 rounded-xl bg-amber-500/15 border border-amber-500/30">
            <p className="text-sm font-medium text-amber-200">
              ORB backtest lane is locked (Soma-first policy). Complete Soma Phase 0 baseline, then set{" "}
              <code className="text-amber-100 bg-white/10 px-1 rounded">gates.allow_orb_backtests=true</code> in{" "}
              <code className="text-amber-100 bg-white/10 px-1 rounded">config/project_state.json</code> to unlock.
            </p>
            {gateState.phase0_baseline_artifact_dir && (
              <p className="text-xs text-amber-200/90 mt-2">
                Baseline artifact dir: <code className="bg-white/10 px-1 rounded">{gateState.phase0_baseline_artifact_dir}</code>
              </p>
            )}
          </div>
        )}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {ORB_BACKTEST_ACTIONS.map((a) => (
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

      {lastResult && (
        <div className="mt-8">
          <GlassCard>
            <div className="px-5 py-3 border-b border-white/10 flex items-center justify-between">
              <div className="flex items-center gap-3">
                <StatusDot variant={lastResult.ok ? "pass" : "fail"} />
                <span className="text-sm font-semibold text-white/95">{lastResult.action}</span>
                <Pill variant={lastResult.ok ? "success" : "fail"}>
                  {lastResult.ok ? "Success" : lastResult.error ? "Error" : `Exit ${lastResult.exitCode}`}
                </Pill>
              </div>
              <span className="text-xs text-white/50">
                {lastResult.durationMs}ms
              </span>
            </div>

            {Boolean(lastResult.error || (lastResult as unknown as Record<string, unknown>).error_class) && (
              <div className="px-5 py-3 bg-red-500/10 border-b border-red-500/20">
                <p className="text-xs text-red-200">{toSafeReactNode(lastResult.error ?? "")}</p>
                {(lastResult as unknown as Record<string, unknown>).error_class != null ? (
                  <p className="text-xs text-red-300 mt-1">
                    error_class: {toSafeReactNode((lastResult as unknown as Record<string, unknown>).error_class)}
                  </p>
                ) : null}
                {(lastResult as unknown as Record<string, unknown>).recommended_next_action != null ? (
                  <p className="text-xs text-amber-200 mt-1">
                    recommended_next_action: {toSafeReactNode((lastResult as unknown as Record<string, unknown>).recommended_next_action)}
                  </p>
                ) : null}
                {(lastResult as unknown as Record<string, unknown>).required_condition != null ? (
                  <p className="text-xs text-amber-200 mt-1">
                    required_condition: {toSafeReactNode((lastResult as unknown as Record<string, unknown>).required_condition)}
                  </p>
                ) : null}
              </div>
            )}

            {lastResult.stdout && (
              <div className="px-5 pt-3 pb-1">
                <p className="text-xs font-medium text-white/50 mb-2">
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

            {!lastResult.stdout && !lastResult.stderr && !lastResult.error && (
              <div className="px-5 py-4">
                <p className="text-xs text-white/50">
                  Command produced no output.
                </p>
              </div>
            )}
          </GlassCard>
        </div>
      )}

      <div className="mt-8 p-4 rounded-2xl glass-surface">
        <p className="text-xs text-white/60">
          <span className="font-semibold">Security:</span> Only allowlisted
          commands are executed. No arbitrary command execution. All traffic goes
          via Host Executor (hostd on localhost). The console binds to 127.0.0.1 only. API requires
          X-OpenClaw-Token header when configured.
        </p>
      </div>
    </div>
  );
}
