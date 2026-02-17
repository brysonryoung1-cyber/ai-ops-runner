"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { GlassCard, GlassButton } from "@/components/glass";
import { EmptyArtifacts } from "@/components/glass";

interface ArtifactDir {
  name: string;
  size?: string;
}

export default function ArtifactsPage() {
  const [dirs, setDirs] = useState<ArtifactDir[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [durationMs, setDurationMs] = useState<number | null>(null);

  const fetchList = async () => {
    setLoading(true);
    setError(null);
    const start = Date.now();
    try {
      const res = await fetch("/api/artifacts/list");
      setDurationMs(Date.now() - start);
      const data = await res.json();
      if (!res.ok) {
        setError(data.error || `HTTP ${res.status}`);
        setDirs([]);
        return;
      }
      setDirs(data.dirs || []);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load");
      setDirs([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchList();
  }, []);

  return (
    <div>
      <div className="flex items-center justify-between mb-8">
        <div>
          <h2 className="text-2xl font-bold text-white/95 tracking-tight">Artifacts</h2>
          <p className="text-sm text-white/60 mt-1">Artifact directories (read-only mount from host)</p>
        </div>
        <GlassButton onClick={() => fetchList()} disabled={loading} size="sm">
          {loading ? "Loading…" : "Refresh"}
        </GlassButton>
      </div>

      {error && (
        <div className="p-4 rounded-2xl glass-surface border border-red-500/20 mb-4">
          <p className="text-sm font-semibold text-red-300">Error</p>
          <p className="text-xs text-red-200/80 mt-1">{error}</p>
        </div>
      )}

      {loading && dirs.length === 0 && !error && (
        <div className="glass-surface rounded-2xl p-12 text-center">
          <div className="inline-block w-6 h-6 border-2 border-blue-400 border-t-transparent rounded-full animate-spin" />
          <p className="text-sm text-white/60 mt-3">Listing artifacts…</p>
        </div>
      )}

      {!loading && dirs.length > 0 && (
        <GlassCard>
          <div className="px-4 py-2.5 border-b border-white/10 flex items-center justify-between">
            <span className="text-xs text-white/50">{dirs.length} directories</span>
            {durationMs != null && (
              <span className="text-xs text-white/50">Fetched in {durationMs}ms</span>
            )}
          </div>
          <ul className="divide-y divide-white/5">
            {dirs.map((dir, i) => (
              <li key={i} className="hover:bg-white/5 transition-colors">
                <Link
                  href={`/artifacts/${encodeURIComponent(dir.name)}`}
                  className="flex items-center justify-between px-5 py-3"
                >
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
                  <div className="flex items-center gap-3">
                    {dir.size && (
                      <span className="text-xs text-white/50 font-mono">{dir.size}</span>
                    )}
                    <span className="text-[11px] font-medium text-blue-400 hover:text-blue-300">
                      Open →
                    </span>
                  </div>
                </Link>
              </li>
            ))}
          </ul>
        </GlassCard>
      )}

      {!loading && dirs.length === 0 && !error && <EmptyArtifacts />}
    </div>
  );
}
