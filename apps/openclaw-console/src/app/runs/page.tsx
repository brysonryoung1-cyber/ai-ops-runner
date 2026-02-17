"use client";

import { useEffect, useState, useCallback } from "react";
import { useToken } from "@/lib/token-context";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { Suspense } from "react";

interface RunData {
  run_id: string;
  project_id: string;
  action: string;
  started_at: string;
  finished_at: string;
  status: "success" | "failure" | "error";
  exit_code: number | null;
  duration_ms: number;
  error_summary: string | null;
  artifact_paths: string[];
}

function statusBadge(status: string): { bg: string; text: string; label: string } {
  switch (status) {
    case "success":
      return { bg: "bg-emerald-500/15", text: "text-emerald-200", label: "Success" };
    case "failure":
      return { bg: "bg-red-500/15", text: "text-red-200", label: "Failure" };
    case "error":
      return { bg: "bg-amber-500/15", text: "text-amber-200", label: "Error" };
    default:
      return { bg: "bg-white/10", text: "text-white/70", label: status };
  }
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.floor(ms / 60000)}m ${Math.round((ms % 60000) / 1000)}s`;
}

function formatTimestamp(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

function formatRelativeTime(isoString: string): string {
  const diff = Date.now() - new Date(isoString).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

function RunsContent() {
  const token = useToken();
  const searchParams = useSearchParams();
  const projectFilter = searchParams.get("project");

  const [runs, setRuns] = useState<RunData[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedRun, setSelectedRun] = useState<RunData | null>(null);

  const fetchRuns = useCallback(async () => {
    setLoading(true);
    try {
      const headers: Record<string, string> = {};
      if (token) headers["X-OpenClaw-Token"] = token;
      const res = await fetch("/api/runs?limit=200", { headers });
      const data = await res.json();
      if (data.ok) {
        let filtered = data.runs;
        if (projectFilter) {
          filtered = filtered.filter((r: RunData) => r.project_id === projectFilter);
        }
        setRuns(filtered);
        setError(null);
      } else {
        setError(data.error || "Failed to load runs");
      }
    } catch (err) {
      setError(`Network error: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setLoading(false);
    }
  }, [token, projectFilter]);

  useEffect(() => {
    fetchRuns();
  }, [fetchRuns]);

  // Group runs by date
  const runsByDate = runs.reduce<Record<string, RunData[]>>((acc, run) => {
    const date = new Date(run.finished_at).toLocaleDateString("en-US", {
      weekday: "long",
      month: "long",
      day: "numeric",
      year: "numeric",
    });
    if (!acc[date]) acc[date] = [];
    acc[date].push(run);
    return acc;
  }, {});

  return (
    <div>
      {/* Page header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h2 className="text-2xl font-bold text-white/95 tracking-tight">Runs</h2>
          <p className="text-sm text-white/60 mt-1">
            {projectFilter
              ? `Run history for ${projectFilter}`
              : "Timeline of runs across all projects"}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {projectFilter && (
            <Link
              href="/runs"
              className="text-xs font-medium text-white/60 hover:text-white/90"
            >
              Clear filter
            </Link>
          )}
          <button
            onClick={fetchRuns}
            disabled={loading}
            className="px-4 py-2 text-xs font-medium rounded-xl bg-white/10 hover:bg-white/15 text-white/90 border border-white/10 disabled:opacity-50"
          >
            {loading ? "Loading…" : "Refresh"}
          </button>
        </div>
      </div>

      {/* Error banner */}
      {error && (
        <div className="mb-6 p-4 rounded-2xl glass-surface border border-red-500/20">
          <p className="text-sm font-semibold text-red-300">Error</p>
          <p className="text-xs text-red-200/80 mt-1">{error}</p>
        </div>
      )}

      {/* Loading */}
      {loading && runs.length === 0 && (
        <div className="glass-surface rounded-2xl p-8 text-center">
          <div className="inline-block w-6 h-6 border-2 border-blue-400 border-t-transparent rounded-full animate-spin" />
          <p className="text-sm text-white/60 mt-3">Loading runs…</p>
        </div>
      )}

      {/* Split view: runs list + detail panel */}
      <div className="flex gap-6">
        {/* Runs timeline */}
        <div className={`${selectedRun ? "w-1/2" : "w-full"} transition-all duration-200`}>
          {Object.entries(runsByDate).map(([date, dateRuns]) => (
            <div key={date} className="mb-6">
              <h3 className="text-xs font-semibold text-white/50 uppercase tracking-wider mb-2 px-1">
                {date}
              </h3>
              <div className="glass-surface rounded-2xl overflow-hidden">
                <ul className="divide-y divide-white/5">
                  {dateRuns.map((run) => {
                    const badge = statusBadge(run.status);
                    const isSelected = selectedRun?.run_id === run.run_id;
                    return (
                      <li
                        key={run.run_id}
                        className={`transition-colors ${isSelected ? "bg-white/10" : "hover:bg-white/5"}`}
                      >
                        <button
                          type="button"
                          onClick={() => setSelectedRun(isSelected ? null : run)}
                          className="w-full flex items-center justify-between px-4 py-3 cursor-pointer text-left"
                        >
                          <div className="flex items-center gap-3 min-w-0 flex-1">
                            <span
                              className={`w-2 h-2 rounded-full flex-shrink-0 ${
                                run.status === "success"
                                  ? "bg-emerald-500"
                                  : run.status === "failure"
                                    ? "bg-red-500"
                                    : "bg-amber-500"
                              }`}
                            />
                            <div className="min-w-0">
                              <div className="flex items-center gap-2">
                                <span className="text-sm font-medium text-white/90">
                                  {run.action}
                                </span>
                                <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded-full ${badge.bg} ${badge.text}`}>
                                  {badge.label}
                                </span>
                              </div>
                              <div className="flex items-center gap-2 mt-0.5">
                                <span className="text-[10px] text-white/50">{run.project_id}</span>
                                <span className="text-[10px] text-white/50">·</span>
                                <span className="text-[10px] text-white/50">
                                  {formatDuration(run.duration_ms)}
                                </span>
                              </div>
                            </div>
                          </div>
                          <span className="text-[11px] text-white/50 flex-shrink-0 ml-3">
                            {formatRelativeTime(run.finished_at)}
                          </span>
                        </button>
                        {/* Anchor link for no-JS fallback */}
                        <div className="px-4 pb-2 -mt-1 flex items-center gap-3">
                          {run.artifact_paths.length > 0 && (
                            <Link
                              href={`/artifacts/runs/${encodeURIComponent(run.run_id)}`}
                              className="text-[10px] font-medium text-blue-400 hover:text-blue-300"
                            >
                              View artifacts →
                            </Link>
                          )}
                          <Link
                            href={`/runs?id=${encodeURIComponent(run.run_id)}`}
                            className="text-[10px] font-medium text-white/40 hover:text-white/70"
                          >
                            Permalink
                          </Link>
                        </div>
                      </li>
                    );
                  })}
                </ul>
              </div>
            </div>
          ))}
        </div>

        {/* Detail panel */}
        {selectedRun && (
          <div className="w-1/2 sticky top-8">
            <div className="glass-surface rounded-2xl overflow-hidden">
              <div className="px-5 py-4 border-b border-white/10">
                <div className="flex items-center justify-between">
                  <div>
                    <h3 className="text-sm font-semibold text-white/95">{selectedRun.action}</h3>
                    <p className="text-xs text-white/50 mt-0.5">
                      Run {selectedRun.run_id}
                    </p>
                  </div>
                  <button
                    onClick={() => setSelectedRun(null)}
                    className="p-1 rounded-lg hover:bg-white/10 transition-colors"
                  >
                    <svg className="w-4 h-4 text-white/50" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                    </svg>
                  </button>
                </div>
              </div>

              {/* Detail body */}
              <div className="p-5 space-y-4">
                {/* Status */}
                <div className="flex items-center gap-3">
                  <span className={`w-3 h-3 rounded-full ${
                    selectedRun.status === "success"
                      ? "bg-emerald-500"
                      : selectedRun.status === "failure"
                        ? "bg-red-500"
                        : "bg-amber-500"
                  }`} />
                  <span className="text-sm font-medium text-white/90 capitalize">
                    {selectedRun.status}
                  </span>
                  {selectedRun.exit_code !== null && (
                    <span className="text-xs text-white/50">
                      Exit code: {selectedRun.exit_code}
                    </span>
                  )}
                </div>

                {/* Metadata grid */}
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <p className="text-[10px] font-semibold text-white/50 uppercase tracking-wider">
                      Project
                    </p>
                    <p className="text-xs text-white/90 mt-0.5 font-mono">
                      <Link href={`/projects/${encodeURIComponent(selectedRun.project_id)}`} className="text-blue-400 hover:text-blue-300">
                        {selectedRun.project_id}
                      </Link>
                    </p>
                  </div>
                  <div>
                    <p className="text-[10px] font-semibold text-white/50 uppercase tracking-wider">
                      Duration
                    </p>
                    <p className="text-xs text-white/90 mt-0.5">
                      {formatDuration(selectedRun.duration_ms)}
                    </p>
                  </div>
                  <div>
                    <p className="text-[10px] font-semibold text-white/50 uppercase tracking-wider">
                      Started
                    </p>
                    <p className="text-xs text-white/90 mt-0.5">
                      {formatTimestamp(selectedRun.started_at)}
                    </p>
                  </div>
                  <div>
                    <p className="text-[10px] font-semibold text-white/50 uppercase tracking-wider">
                      Finished
                    </p>
                    <p className="text-xs text-white/90 mt-0.5">
                      {formatTimestamp(selectedRun.finished_at)}
                    </p>
                  </div>
                </div>

                {/* Error summary */}
                {selectedRun.error_summary && (
                  <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/20">
                    <p className="text-[10px] font-semibold text-red-300 uppercase tracking-wider mb-1">
                      Error
                    </p>
                    <p className="text-xs text-red-200 font-mono whitespace-pre-wrap">
                      {selectedRun.error_summary}
                    </p>
                  </div>
                )}

                {/* Artifact paths */}
                {selectedRun.artifact_paths.length > 0 && (
                  <div>
                    <p className="text-[10px] font-semibold text-white/50 uppercase tracking-wider mb-1">
                      Artifacts
                    </p>
                    <ul className="space-y-1">
                      {selectedRun.artifact_paths.map((path, i) => (
                        <li key={i}>
                          <Link
                            href={`/artifacts/${path.split("/").map(encodeURIComponent).join("/")}`}
                            className="text-xs text-blue-400 hover:text-blue-300 font-mono bg-white/5 rounded px-2 py-1 block"
                          >
                            {path}
                          </Link>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Empty state */}
      {!loading && runs.length === 0 && !error && (
        <div className="glass-surface rounded-2xl p-12 text-center">
          <p className="text-sm text-white/70">
            {projectFilter
              ? `No runs found for project "${projectFilter}".`
              : "No runs recorded yet. Execute an action to see run history."}
          </p>
        </div>
      )}

      {/* Stats footer */}
      {runs.length > 0 && (
        <div className="mt-6 p-4 rounded-2xl glass-surface">
          <div className="flex items-center gap-6">
            <div className="flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-emerald-500" />
              <span className="text-xs text-white/60">
                {runs.filter((r) => r.status === "success").length} successful
              </span>
            </div>
            <div className="flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-red-500" />
              <span className="text-xs text-white/60">
                {runs.filter((r) => r.status === "failure").length} failed
              </span>
            </div>
            <div className="flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-amber-500" />
              <span className="text-xs text-white/60">
                {runs.filter((r) => r.status === "error").length} errors
              </span>
            </div>
            <span className="text-xs text-white/60 ml-auto">
              Showing {runs.length} runs
            </span>
          </div>
        </div>
      )}
    </div>
  );
}

export default function RunsPage() {
  return (
    <Suspense fallback={
      <div className="p-8 text-center">
        <div className="inline-block w-6 h-6 border-2 border-blue-400 border-t-transparent rounded-full animate-spin" />
        <p className="text-sm text-white/60 mt-3">Loading…</p>
      </div>
    }>
      <RunsContent />
    </Suspense>
  );
}
