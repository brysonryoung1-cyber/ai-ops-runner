"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { GlassCard } from "@/components/glass";

interface BrowseEntry {
  name: string;
  type: "dir" | "file";
  size?: number;
}

interface BrowseData {
  entries: BrowseEntry[];
  content?: string;
  contentType?: "markdown" | "json" | "text" | "binary";
  fileName?: string;
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function ArtifactBrowserPage() {
  const params = useParams();
  const pathSegments = (params.path as string[]) || [];
  const currentPath = pathSegments.map(decodeURIComponent).join("/");

  const [data, setData] = useState<BrowseData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    const browsePath = pathSegments.join("/");
    fetch(`/api/artifacts/browse?path=${encodeURIComponent(browsePath)}`)
      .then(async (res) => {
        const json = await res.json();
        if (!res.ok) {
          setError(json.error || `HTTP ${res.status}`);
          setData(null);
        } else {
          setData(json);
        }
      })
      .catch((e) => {
        setError(e instanceof Error ? e.message : "Failed to load");
        setData(null);
      })
      .finally(() => setLoading(false));
  }, [currentPath]); // eslint-disable-line react-hooks/exhaustive-deps

  const breadcrumbs = [
    { label: "Artifacts", href: "/artifacts" },
    ...pathSegments.map((seg, i) => ({
      label: decodeURIComponent(seg),
      href: "/artifacts/" + pathSegments.slice(0, i + 1).map(encodeURIComponent).join("/"),
    })),
  ];

  return (
    <div>
      {/* Breadcrumbs */}
      <nav aria-label="Breadcrumb" className="mb-6">
        <ol className="flex items-center gap-1.5 text-sm">
          {breadcrumbs.map((crumb, i) => (
            <li key={crumb.href} className="flex items-center gap-1.5">
              {i > 0 && <span className="text-white/30">/</span>}
              {i < breadcrumbs.length - 1 ? (
                <Link href={crumb.href} className="text-blue-400 hover:text-blue-300 hover:underline">
                  {crumb.label}
                </Link>
              ) : (
                <span className="text-white/90 font-medium">{crumb.label}</span>
              )}
            </li>
          ))}
        </ol>
      </nav>

      <div className="flex items-center justify-between mb-8">
        <div>
          <h2 className="text-2xl font-bold text-white/95 tracking-tight">
            {pathSegments.length > 0 ? decodeURIComponent(pathSegments[pathSegments.length - 1]) : "Artifacts"}
          </h2>
          <p className="text-sm text-white/60 mt-1 font-mono">{currentPath || "/"}</p>
        </div>
        <div className="flex items-center gap-2">
          <Link
            href="/artifacts"
            className="px-3 py-1.5 text-xs font-medium rounded-xl bg-white/10 hover:bg-white/15 text-white/90 border border-white/10"
          >
            ← Root
          </Link>
        </div>
      </div>

      {error && (
        <div className="p-4 rounded-2xl glass-surface border border-red-500/20 mb-4">
          <p className="text-sm font-semibold text-red-300">Error</p>
          <p className="text-xs text-red-200/80 mt-1">{error}</p>
        </div>
      )}

      {loading && (
        <div className="glass-surface rounded-2xl p-12 text-center">
          <div className="inline-block w-6 h-6 border-2 border-blue-400 border-t-transparent rounded-full animate-spin" />
          <p className="text-sm text-white/60 mt-3">Loading…</p>
        </div>
      )}

      {/* Directory listing */}
      {!loading && data && data.entries && data.entries.length > 0 && (
        <GlassCard>
          <div className="px-4 py-2.5 border-b border-white/10">
            <span className="text-xs text-white/50">{data.entries.length} entries</span>
          </div>
          <ul className="divide-y divide-white/5">
            {data.entries.map((entry) => {
              const entryHref = `/artifacts/${pathSegments.map(encodeURIComponent).join("/")}/${encodeURIComponent(entry.name)}`;
              return (
                <li key={entry.name} className="hover:bg-white/5 transition-colors">
                  <Link href={entryHref} className="flex items-center justify-between px-5 py-3">
                    <div className="flex items-center gap-3">
                      {entry.type === "dir" ? (
                        <svg className="w-4 h-4 text-white/40 flex-shrink-0" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" d="M2.25 12.75V12A2.25 2.25 0 014.5 9.75h15A2.25 2.25 0 0121.75 12v.75m-8.69-6.44l-2.12-2.12a1.5 1.5 0 00-1.061-.44H4.5A2.25 2.25 0 002.25 6v12a2.25 2.25 0 002.25 2.25h15A2.25 2.25 0 0021.75 18V9a2.25 2.25 0 00-2.25-2.25h-5.379a1.5 1.5 0 01-1.06-.44z" />
                        </svg>
                      ) : (
                        <svg className="w-4 h-4 text-white/40 flex-shrink-0" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
                        </svg>
                      )}
                      <span className="text-sm font-mono text-white/90">{entry.name}</span>
                    </div>
                    <div className="flex items-center gap-3">
                      {entry.size != null && (
                        <span className="text-xs text-white/50 font-mono">{formatSize(entry.size)}</span>
                      )}
                      <span className="text-[11px] font-medium text-blue-400">
                        {entry.type === "dir" ? "Open →" : "View →"}
                      </span>
                    </div>
                  </Link>
                </li>
              );
            })}
          </ul>
        </GlassCard>
      )}

      {/* File content viewer */}
      {!loading && data && data.content != null && (
        <GlassCard>
          <div className="px-4 py-2.5 border-b border-white/10 flex items-center justify-between">
            <span className="text-xs text-white/50 font-mono">{data.fileName}</span>
            <a
              href={`/api/artifacts/browse?path=${encodeURIComponent(pathSegments.join("/"))}&download=1`}
              className="text-[11px] font-medium text-blue-400 hover:text-blue-300"
              download
            >
              Download
            </a>
          </div>
          <div className="p-5">
            {data.contentType === "json" && (
              <pre className="output-block text-xs overflow-auto max-h-[600px]">{data.content}</pre>
            )}
            {data.contentType === "markdown" && (
              <div className="prose prose-invert prose-sm max-w-none">
                <pre className="whitespace-pre-wrap text-sm text-white/90 leading-relaxed">{data.content}</pre>
              </div>
            )}
            {(data.contentType === "text" || !data.contentType) && (
              <pre className="output-block text-xs overflow-auto max-h-[600px]">{data.content}</pre>
            )}
            {data.contentType === "binary" && (
              <div className="text-center py-8">
                <p className="text-sm text-white/60">Binary file — use the download link above.</p>
              </div>
            )}
          </div>
        </GlassCard>
      )}

      {/* Empty directory */}
      {!loading && data && data.entries && data.entries.length === 0 && !data.content && (
        <div className="glass-surface rounded-2xl p-12 text-center">
          <p className="text-sm text-white/70">This directory is empty.</p>
        </div>
      )}
    </div>
  );
}
