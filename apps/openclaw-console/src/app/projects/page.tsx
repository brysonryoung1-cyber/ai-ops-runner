"use client";

import { useEffect, useState, useCallback } from "react";
import { useToken } from "@/lib/token-context";
import Link from "next/link";
import { GlassCard, GlassButton, StatusDot } from "@/components/glass";
import { SkeletonCard } from "@/components/glass";

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

function statusColor(project: ProjectData): { dot: "pass" | "fail" | "warn" | "idle"; label: string; labelColor: string } {
  if (!project.enabled) return { dot: "idle", label: "Disabled", labelColor: "text-white/40" };
  if (!project.last_run) return { dot: "warn", label: "No runs", labelColor: "text-amber-400" };
  if (project.last_run.status === "success") return { dot: "pass", label: "Healthy", labelColor: "text-emerald-400" };
  return { dot: "fail", label: project.last_run.status === "error" ? "Error" : "Failing", labelColor: "text-red-400" };
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
      <div className="flex items-center justify-between mb-8">
        <div>
          <h2 className="text-2xl font-bold text-white/95 tracking-tight">Projects</h2>
          <p className="text-sm text-white/60 mt-1">All registered projects and their current status</p>
        </div>
        <GlassButton onClick={fetchProjects} disabled={loading} size="sm">
          {loading ? "Loading…" : "Refresh"}
        </GlassButton>
      </div>

      {error && (
        <div className="mb-6 p-4 rounded-2xl glass-surface border border-red-500/20">
          <p className="text-sm font-semibold text-red-300">Error</p>
          <p className="text-xs text-red-200/80 mt-1">{error}</p>
        </div>
      )}

      {loading && projects.length === 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {[1, 2, 3, 4].map((i) => (
            <SkeletonCard key={i} />
          ))}
        </div>
      )}

      {/* Project cards */}
      {!loading && projects.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {projects.map((project) => {
            const status = statusColor(project);
            return (
              <GlassCard key={project.id}>
                <div className="p-5 pb-3">
                  <div className="flex items-start justify-between mb-2">
                    <div className="flex-1 min-w-0">
                      <h3 className="text-sm font-semibold text-white/95 truncate">{project.name}</h3>
                      <p className="text-xs text-white/60 mt-0.5 line-clamp-2">{project.description}</p>
                    </div>
                    <div className="flex items-center gap-2 ml-3 flex-shrink-0">
                      <StatusDot variant={status.dot} />
                      <span className={`text-xs font-medium ${status.labelColor}`}>{status.label}</span>
                    </div>
                  </div>

                  <div className="flex flex-wrap gap-1.5 mt-2">
                    {project.tags.map((tag) => (
                      <span key={tag} className="inline-flex px-2 py-0.5 text-[10px] font-medium text-white/60 bg-white/10 rounded-full">
                        {tag}
                      </span>
                    ))}
                  </div>
                </div>

                <div className="border-t border-white/10" />

                <div className="px-5 py-3">
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <p className="text-[10px] font-semibold text-white/50 uppercase tracking-wider mb-0.5">Last Run</p>
                      {project.last_run ? (
                        <div>
                          <p className="text-xs text-white/90 font-medium">{formatRelativeTime(project.last_run.finished_at)}</p>
                          <p className={`text-[10px] ${project.last_run.status === "success" ? "text-emerald-400" : "text-red-400"}`}>
                            {project.last_run.action} — {project.last_run.status}
                          </p>
                        </div>
                      ) : (
                        <p className="text-xs text-white/50">No runs yet</p>
                      )}
                    </div>

                    <div>
                      <p className="text-[10px] font-semibold text-white/50 uppercase tracking-wider mb-0.5">Schedule</p>
                      {project.schedules.length > 0 ? (
                        <p className="text-xs text-white/90">{project.schedules[0].label}</p>
                      ) : (
                        <p className="text-xs text-white/50">On-demand</p>
                      )}
                    </div>
                  </div>

                  {project.last_run?.error_summary && (
                    <div className="mt-2 p-2 rounded-lg bg-red-500/10 border border-red-500/20">
                      <p className="text-[10px] font-semibold text-red-300 uppercase tracking-wider mb-0.5">Last Error</p>
                      <p className="text-xs text-red-200 line-clamp-2">{project.last_run.error_summary}</p>
                    </div>
                  )}
                </div>

                <div className="border-t border-white/10 px-5 py-2.5 flex items-center justify-between">
                  <div className="flex items-center gap-1.5">
                    <span className="text-[10px] text-white/50">
                      {project.workflows.length} workflow{project.workflows.length !== 1 ? "s" : ""}
                    </span>
                    <span className="text-white/40">·</span>
                    <span className="text-[10px] text-white/50">
                      {project.notification_flags.channels.join(", ") || "no alerts"}
                    </span>
                  </div>
                  {project.last_run && (
                    <Link href={`/runs?project=${project.id}`} className="text-[11px] font-medium text-blue-400 hover:text-blue-300">
                      View runs
                    </Link>
                  )}
                </div>
              </GlassCard>
            );
          })}
        </div>
      )}

      {!loading && projects.length === 0 && !error && (
        <div className="glass-surface rounded-2xl p-12 text-center">
          <p className="text-sm text-white/70">No projects registered. Add projects to config/projects.json.</p>
        </div>
      )}

      <div className="mt-8 p-4 rounded-2xl glass-surface">
        <p className="text-xs text-white/60">
          <span className="font-semibold">Registry:</span> Projects are defined in{" "}
          <code className="text-[11px] bg-white/10 px-1 py-0.5 rounded text-white/80">
            config/projects.json
          </code>
          . Edit the registry to add/remove projects. Schema validation is fail-closed.
        </p>
      </div>
    </div>
  );
}
