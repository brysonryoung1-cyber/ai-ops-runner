"use client";

import { useEffect, useState } from "react";
import StatusCard from "@/components/StatusCard";
import CollapsibleOutput from "@/components/CollapsibleOutput";
import { useExec, ExecResult } from "@/lib/hooks";

type CardStatus = "pass" | "fail" | "loading" | "idle" | "warn";

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

export default function OverviewPage() {
  const { exec, loading, results } = useExec();
  const [connected, setConnected] = useState<boolean | null>(null);
  const [connError, setConnError] = useState<string | null>(null);

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

  // Auto-refresh overview data on mount
  useEffect(() => {
    if (connected === true) {
      exec("doctor");
      exec("ports");
      exec("timer");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connected]);

  const doctorResult = results["doctor"];
  const portsResult = results["ports"];
  const timerResult = results["timer"];

  const doctorSummary = doctorResult ? parseDoctorSummary(doctorResult.stdout) : "";
  const portInfo = portsResult ? parsePortSummary(portsResult.stdout) : null;
  const timerStatus = timerResult ? parseTimerStatus(timerResult.stdout) : "";
  const dockerStatus = doctorResult ? parseDockerStatus(doctorResult.stdout) : "";

  return (
    <div>
      {/* Page header */}
      <div className="mb-8">
        <h2 className="text-2xl font-bold text-apple-text tracking-tight">
          Overview
        </h2>
        <p className="text-sm text-apple-muted mt-1">
          System health for aiops-1 via Tailscale SSH
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

      {/* Refresh button */}
      <div className="mt-6 flex justify-end">
        <button
          onClick={() => {
            exec("doctor");
            exec("ports");
            exec("timer");
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
