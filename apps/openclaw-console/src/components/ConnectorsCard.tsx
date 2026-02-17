"use client";

import type { ExecResult } from "@/lib/hooks";

export function parseConnectorsStatus(stdout: string | undefined): {
  kajabi: "connected" | "not_connected" | "unknown";
  gmail: "connected" | "not_connected" | "unknown";
} {
  if (!stdout?.trim()) return { kajabi: "unknown", gmail: "unknown" };
  try {
    const d = JSON.parse(stdout.trim());
    return {
      kajabi: d.kajabi === "connected" ? "connected" : "not_connected",
      gmail: d.gmail === "connected" ? "connected" : "not_connected",
    };
  } catch {
    return { kajabi: "unknown", gmail: "unknown" };
  }
}

interface ConnectorsCardProps {
  result?: ExecResult;
  loading: boolean;
  onExec: (action: string) => void;
  /** Optional: use glass (openclaw) styling when true; otherwise apple-style. */
  variant?: "glass" | "apple";
}

export default function ConnectorsCard({
  result,
  loading,
  onExec,
  variant = "apple",
}: ConnectorsCardProps) {
  const status = parseConnectorsStatus(result?.stdout);

  const cardClass =
    variant === "glass"
      ? "mb-6 p-4 rounded-2xl glass-surface border border-white/10"
      : "mb-8 p-4 rounded-apple bg-apple-card border border-apple-border";
  const titleClass =
    variant === "glass"
      ? "text-sm font-semibold text-white/95 mb-3"
      : "text-sm font-semibold text-apple-text mb-3";
  const labelClass = variant === "glass" ? "text-sm text-white/70" : "text-sm text-apple-muted";
  const btnBase =
    variant === "glass"
      ? "px-3 py-1.5 text-xs font-medium rounded hover:opacity-90 disabled:opacity-50"
      : "px-3 py-1.5 text-xs font-medium rounded hover:bg-gray-200 disabled:opacity-50";

  return (
    <div className={cardClass}>
      <h3 className={titleClass}>Connectors</h3>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="flex items-center justify-between">
          <span className={labelClass}>Kajabi</span>
          <span
            className={`text-xs font-medium px-2 py-1 rounded ${
              status.kajabi === "connected"
                ? "bg-green-100 text-green-700"
                : status.kajabi === "not_connected"
                  ? "bg-amber-100 text-amber-700"
                  : "bg-gray-100 text-gray-600"
            }`}
          >
            {loading ? "Checking…" : status.kajabi === "connected" ? "Connected" : status.kajabi === "not_connected" ? "Not connected" : "—"}
          </span>
        </div>
        <div className="flex items-center justify-between">
          <span className={labelClass}>Gmail</span>
          <span
            className={`text-xs font-medium px-2 py-1 rounded ${
              status.gmail === "connected"
                ? "bg-green-100 text-green-700"
                : status.gmail === "not_connected"
                  ? "bg-amber-100 text-amber-700"
                  : "bg-gray-100 text-gray-600"
            }`}
          >
            {loading ? "Checking…" : status.gmail === "connected" ? "Connected" : status.gmail === "not_connected" ? "Not connected" : "—"}
          </span>
        </div>
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => onExec("soma_connectors_status")}
          disabled={!!loading}
          className={`${btnBase} ${variant === "glass" ? "bg-white/10 text-white/90" : "bg-gray-100"}`}
        >
          {loading ? "Checking…" : "Check Connectors"}
        </button>
        <button
          type="button"
          onClick={() => onExec("soma_kajabi_bootstrap_start")}
          disabled={!!loading}
          className={`${btnBase} ${variant === "glass" ? "bg-blue-500/30 text-blue-200" : "bg-blue-50 text-blue-700"}`}
        >
          Kajabi Bootstrap
        </button>
        <button
          type="button"
          onClick={() => onExec("soma_kajabi_gmail_connect_start")}
          disabled={!!loading}
          className={`${btnBase} ${variant === "glass" ? "bg-blue-500/30 text-blue-200" : "bg-blue-50 text-blue-700"}`}
        >
          Gmail Connect
        </button>
      </div>
    </div>
  );
}
