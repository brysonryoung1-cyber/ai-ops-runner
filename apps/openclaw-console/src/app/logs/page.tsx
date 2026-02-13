"use client";

import { useEffect } from "react";
import { useExec } from "@/lib/hooks";

export default function LogsPage() {
  const { exec, loading, results } = useExec();
  const journalResult = results["journal"];

  useEffect(() => {
    exec("journal");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const rawOutput = journalResult
    ? journalResult.stdout.replace(/\x1b\[[0-9;]*m/g, "")
    : "";

  const lines = rawOutput.split("\n").filter((l) => l.trim());

  return (
    <div>
      <div className="flex items-center justify-between mb-8">
        <div>
          <h2 className="text-2xl font-bold text-apple-text tracking-tight">
            Guard Logs
          </h2>
          <p className="text-sm text-apple-muted mt-1">
            Last 200 lines from openclaw-guard.service journal
          </p>
        </div>
        <button
          onClick={() => exec("journal")}
          disabled={loading === "journal"}
          className="px-4 py-2 text-xs font-medium text-apple-blue bg-blue-50 rounded-lg hover:bg-blue-100 transition-colors disabled:opacity-50"
        >
          {loading === "journal" ? "Loading…" : "Refresh"}
        </button>
      </div>

      {/* Error state */}
      {journalResult && !journalResult.ok && journalResult.error && (
        <div className="p-4 rounded-apple bg-red-50 border border-red-200 mb-4">
          <p className="text-sm font-semibold text-apple-red">Error</p>
          <p className="text-xs text-red-600 mt-1">{journalResult.error}</p>
        </div>
      )}

      {/* Loading state */}
      {loading === "journal" && !journalResult && (
        <div className="p-8 text-center">
          <div className="inline-block w-6 h-6 border-2 border-apple-blue border-t-transparent rounded-full animate-spin" />
          <p className="text-sm text-apple-muted mt-3">
            Fetching logs from aiops-1…
          </p>
        </div>
      )}

      {/* Log output */}
      {lines.length > 0 && (
        <div className="bg-apple-card rounded-apple border border-apple-border shadow-apple overflow-hidden">
          {/* Stats bar */}
          <div className="px-4 py-2.5 bg-gray-50 border-b border-apple-border flex items-center justify-between">
            <span className="text-xs text-apple-muted">
              {lines.length} lines
            </span>
            {journalResult && (
              <span className="text-xs text-apple-muted">
                Fetched in {journalResult.durationMs}ms
              </span>
            )}
          </div>
          <div className="output-block rounded-none border-0 max-h-[600px]">
            {rawOutput}
          </div>
        </div>
      )}

      {/* Empty state */}
      {journalResult && journalResult.ok && lines.length === 0 && (
        <div className="p-8 text-center bg-apple-card rounded-apple border border-apple-border">
          <p className="text-sm text-apple-muted">
            No guard logs found. The guard timer may not have run yet.
          </p>
        </div>
      )}

      {/* No sshd / stderr fallback */}
      {journalResult && !journalResult.ok && journalResult.stderr && (
        <div className="mt-4 bg-apple-card rounded-apple border border-apple-border shadow-apple overflow-hidden">
          <div className="px-4 py-2.5 bg-gray-50 border-b border-apple-border">
            <span className="text-xs text-apple-muted">stderr</span>
          </div>
          <div className="output-block rounded-none border-0">
            {journalResult.stderr.replace(/\x1b\[[0-9;]*m/g, "")}
          </div>
        </div>
      )}
    </div>
  );
}
