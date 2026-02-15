"use client";

import { useEffect, useState, useCallback } from "react";
import { useToken } from "@/lib/token-context";
import Link from "next/link";

interface LastRun {
  run_id: string;
  action: string;
  status: "success" | "failure" | "error";
  finished_at: string;
  duration_ms: number;
  error_summary: string | null;
}

interface ProjectData {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
  workflows: string[];
  schedules: { workflow: string; cron: string; label: string }[];
  notification_flags: {
    on_success: boolean;
    on_failure: boolean;
    on_recovery: boolean;
    channels: string[];
  };
  tags: string[];
  last_run: LastRun | null;
}

function statusColor(project: ProjectData): {
  dot: string;
  label: string;
  labelColor: string;
  bgColor: string;
  borderColor: string;
} {
  if (!project.enabled) {
    return {
      dot: "bg-gray-400",
      label: "Disabled",
      labelColor: "text-gray-500",
      bgColor: "bg-gray-50",
      borderColor: "border-gray-200",
    };
  }
  if (!project.last_run) {
    return {
      dot: "bg-apple-orange",
      label: "No runs",
      labelColor: "text-apple-orange",
      bgColor: "bg-orange-50",
      borderColor: "border-orange-200",
    };
  }
  if (project.last_run.status === "success") {
    return {
      dot: "bg-apple-green",
      label: "Healthy",
      labelColor: "text-apple-green",
      bgColor: "bg-green-50",
      borderColor: "border-green-200",
    };
  }
  return {
    dot: "bg-apple-red",
    label: project.last_run.status === "error" ? "Error" : "Failing",
    labelColor: "text-apple-red",
    bgColor: "bg-red-50",
    borderColor: "border-red-200",
  };
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

function nextRunFromCron(cron: string): string {
  // Simplified: just show the cron expression label
  return cron;
}

export default function ProjectsPage() {
  const token = useToken();
  const [projects, setProjects] = useState<ProjectData[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchProjects = useCallback(async () => {
    setLoading(true);
    try {
      const headers: Record<string, string> = {};
      if (token) headers["X-OpenClaw-Token"] = token;
      const res = await fetch("/api/projects", { headers });
      const data = await res.json();
      if (data.ok) {
        setProjects(data.projects);
        setError(null);
      } else {
        setError(data.error || "Failed to load projects");
      }
    } catch (err) {
      setError(`Network error: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    fetchProjects();
  }, [fetchProjects]);

  return (
    <div>
      {/* Page header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h2 className="text-2xl font-bold text-apple-text tracking-tight">
            Projects
          </h2>
          <p className="text-sm text-apple-muted mt-1">
            All registered projects and their current status
          </p>
        </div>
        <button
          onClick={fetchProjects}
          disabled={loading}
          className="px-4 py-2 text-xs font-medium text-apple-blue bg-blue-50 rounded-lg hover:bg-blue-100 transition-colors disabled:opacity-50"
        >
          {loading ? "Loading…" : "Refresh"}
        </button>
      </div>

      {/* Error banner */}
      {error && (
        <div className="mb-6 p-4 rounded-apple bg-red-50 border border-red-200">
          <p className="text-sm font-semibold text-apple-red">Error</p>
          <p className="text-xs text-red-600 mt-1">{error}</p>
        </div>
      )}

      {/* Loading skeleton */}
      {loading && projects.length === 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {[1, 2, 3, 4].map((i) => (
            <div
              key={i}
              className="bg-apple-card rounded-apple border border-apple-border shadow-apple p-5 animate-pulse"
            >
              <div className="h-4 bg-gray-200 rounded w-3/4 mb-2" />
              <div className="h-3 bg-gray-100 rounded w-full mb-4" />
              <div className="h-8 bg-gray-100 rounded w-1/2" />
            </div>
          ))}
        </div>
      )}

      {/* Project cards */}
      {!loading && projects.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {projects.map((project) => {
            const status = statusColor(project);
            return (
              <div
                key={project.id}
                className="bg-apple-card rounded-apple border border-apple-border shadow-apple overflow-hidden"
              >
                {/* Card header */}
                <div className="p-5 pb-3">
                  <div className="flex items-start justify-between mb-2">
                    <div className="flex-1 min-w-0">
                      <h3 className="text-sm font-semibold text-apple-text truncate">
                        {project.name}
                      </h3>
                      <p className="text-xs text-apple-muted mt-0.5 line-clamp-2">
                        {project.description}
                      </p>
                    </div>
                    <div className="flex items-center gap-2 ml-3 flex-shrink-0">
                      <span className={`w-2.5 h-2.5 rounded-full ${status.dot}`} />
                      <span className={`text-xs font-medium ${status.labelColor}`}>
                        {status.label}
                      </span>
                    </div>
                  </div>

                  {/* Tags */}
                  <div className="flex flex-wrap gap-1.5 mt-2">
                    {project.tags.map((tag) => (
                      <span
                        key={tag}
                        className="inline-flex px-2 py-0.5 text-[10px] font-medium text-apple-muted bg-gray-100 rounded-full"
                      >
                        {tag}
                      </span>
                    ))}
                  </div>
                </div>

                {/* Divider */}
                <div className="border-t border-apple-border" />

                {/* Stats row */}
                <div className="px-5 py-3 bg-gray-50/50">
                  <div className="grid grid-cols-2 gap-3">
                    {/* Last run */}
                    <div>
                      <p className="text-[10px] font-semibold text-apple-muted uppercase tracking-wider mb-0.5">
                        Last Run
                      </p>
                      {project.last_run ? (
                        <div>
                          <p className="text-xs text-apple-text font-medium">
                            {formatRelativeTime(project.last_run.finished_at)}
                          </p>
                          <p className={`text-[10px] ${
                            project.last_run.status === "success"
                              ? "text-apple-green"
                              : "text-apple-red"
                          }`}>
                            {project.last_run.action} — {project.last_run.status}
                          </p>
                        </div>
                      ) : (
                        <p className="text-xs text-apple-muted">No runs yet</p>
                      )}
                    </div>

                    {/* Next run / schedules */}
                    <div>
                      <p className="text-[10px] font-semibold text-apple-muted uppercase tracking-wider mb-0.5">
                        Schedule
                      </p>
                      {project.schedules.length > 0 ? (
                        <p className="text-xs text-apple-text">
                          {project.schedules[0].label}
                        </p>
                      ) : (
                        <p className="text-xs text-apple-muted">On-demand</p>
                      )}
                    </div>
                  </div>

                  {/* Error summary */}
                  {project.last_run?.error_summary && (
                    <div className="mt-2 p-2 rounded-lg bg-red-50 border border-red-100">
                      <p className="text-[10px] font-semibold text-apple-red uppercase tracking-wider mb-0.5">
                        Last Error
                      </p>
                      <p className="text-xs text-red-600 line-clamp-2">
                        {project.last_run.error_summary}
                      </p>
                    </div>
                  )}
                </div>

                {/* Quick actions */}
                <div className="border-t border-apple-border px-5 py-2.5 flex items-center justify-between">
                  <div className="flex items-center gap-1.5">
                    <span className="text-[10px] text-apple-muted">
                      {project.workflows.length} workflow{project.workflows.length !== 1 ? "s" : ""}
                    </span>
                    <span className="text-apple-muted">·</span>
                    <span className="text-[10px] text-apple-muted">
                      {project.notification_flags.channels.join(", ") || "no alerts"}
                    </span>
                  </div>
                  {project.last_run && (
                    <Link
                      href={`/runs?project=${project.id}`}
                      className="text-[11px] font-medium text-apple-blue hover:underline"
                    >
                      View runs
                    </Link>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Empty state */}
      {!loading && projects.length === 0 && !error && (
        <div className="p-8 text-center bg-apple-card rounded-apple border border-apple-border">
          <p className="text-sm text-apple-muted">
            No projects registered. Add projects to config/projects.json.
          </p>
        </div>
      )}

      {/* Info footer */}
      <div className="mt-8 p-4 rounded-apple bg-gray-50 border border-apple-border">
        <p className="text-xs text-apple-muted">
          <span className="font-semibold">Registry:</span> Projects are defined in{" "}
          <code className="text-[11px] bg-gray-200/60 px-1 py-0.5 rounded">
            config/projects.json
          </code>
          . Edit the registry to add/remove projects. Schema validation is fail-closed.
        </p>
      </div>
    </div>
  );
}
