"use client";

import { useEffect } from "react";
import { useExec } from "@/lib/hooks";
import { GlassCard, GlassButton } from "@/components/glass";
import { EmptyArtifacts } from "@/components/glass";

interface ArtifactDir {
  name: string;
  size?: string;
}

function parseArtifacts(stdout: string): {
  dirs: ArtifactDir[];
  sizes: Map<string, string>;
} {
  const raw = stdout.replace(/\x1b\[[0-9;]*m/g, "");
  const parts = raw.split("---");

  // First section: ls -1dt output (sorted by date)
  const dirLines = (parts[0] || "")
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean);

  // Second section: du -sh output (sorted by size)
  const sizeLines = (parts[1] || "")
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean);

  const sizes = new Map<string, string>();
  for (const line of sizeLines) {
    const match = line.match(/^(\S+)\s+(.+)$/);
    if (match) {
      sizes.set(match[2], match[1]);
    }
  }

  const dirs: ArtifactDir[] = dirLines.map((name) => ({
    name,
    size: sizes.get(name),
  }));

  return { dirs, sizes };
}

export default function ArtifactsPage() {
  const { exec, loading, results } = useExec();
  const artifactsResult = results["artifacts"];

  useEffect(() => {
    exec("artifacts");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const parsed = artifactsResult
    ? parseArtifacts(artifactsResult.stdout)
    : null;

  return (
    <div>
      <div className="flex items-center justify-between mb-8">
        <div>
          <h2 className="text-2xl font-bold text-white/95 tracking-tight">Artifacts</h2>
          <p className="text-sm text-white/60 mt-1">Latest job artifact directories on aiops-1</p>
        </div>
        <GlassButton onClick={() => exec("artifacts")} disabled={loading === "artifacts"} size="sm">
          {loading === "artifacts" ? "Loading…" : "Refresh"}
        </GlassButton>
      </div>

      {artifactsResult && !artifactsResult.ok && artifactsResult.error && (
        <div className="p-4 rounded-2xl glass-surface border border-red-500/20 mb-4">
          <p className="text-sm font-semibold text-red-300">Error</p>
          <p className="text-xs text-red-200/80 mt-1">{artifactsResult.error}</p>
        </div>
      )}

      {loading === "artifacts" && !artifactsResult && (
        <div className="glass-surface rounded-2xl p-12 text-center">
          <div className="inline-block w-6 h-6 border-2 border-blue-400 border-t-transparent rounded-full animate-spin" />
          <p className="text-sm text-white/60 mt-3">Listing artifacts on aiops-1…</p>
        </div>
      )}

      {parsed && parsed.dirs.length > 0 && (
        <GlassCard>
          <div className="px-4 py-2.5 border-b border-white/10 flex items-center justify-between">
            <span className="text-xs text-white/50">{parsed.dirs.length} directories (most recent first)</span>
            {artifactsResult && (
              <span className="text-xs text-white/50">
                Fetched in {artifactsResult.durationMs}ms
              </span>
            )}
          </div>
          <ul className="divide-y divide-white/5">
            {parsed.dirs.map((dir, i) => (
              <li key={i} className="flex items-center justify-between px-5 py-3 hover:bg-white/5 transition-colors">
                <div className="flex items-center gap-3">
                  <svg className="w-4 h-4 text-white/40 flex-shrink-0"
                    fill="none"
                    viewBox="0 0 24 24"
                    strokeWidth={1.5}
                    stroke="currentColor"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      d="M2.25 12.75V12A2.25 2.25 0 014.5 9.75h15A2.25 2.25 0 0121.75 12v.75m-8.69-6.44l-2.12-2.12a1.5 1.5 0 00-1.061-.44H4.5A2.25 2.25 0 002.25 6v12a2.25 2.25 0 002.25 2.25h15A2.25 2.25 0 0021.75 18V9a2.25 2.25 0 00-2.25-2.25h-5.379a1.5 1.5 0 01-1.06-.44z"
                    />
                  </svg>
                  <span className="text-sm font-mono text-white/90">{dir.name}</span>
                </div>
                {dir.size && (
                  <span className="text-xs text-white/50 font-mono">
                    {dir.size}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </GlassCard>
      )}

      {parsed && parsed.dirs.length === 0 && <EmptyArtifacts />}

      {artifactsResult && artifactsResult.stdout && (
        <div className="mt-4">
          <details className="group">
            <summary className="text-xs text-white/50 cursor-pointer hover:text-white/90 transition-colors">
              Show raw output
            </summary>
            <div className="output-block mt-2">
              {artifactsResult.stdout.replace(/\x1b\[[0-9;]*m/g, "")}
            </div>
          </details>
        </div>
      )}
    </div>
  );
}
