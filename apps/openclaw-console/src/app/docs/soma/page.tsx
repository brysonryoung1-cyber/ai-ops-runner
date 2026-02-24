"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { GlassCard } from "@/components/glass";

export default function SomaDocsPage() {
  const [spec, setSpec] = useState<string | null>(null);
  const [checklist, setChecklist] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      fetch("/api/docs/SOMA_LOCKED_SPEC.md").then((r) => (r.ok ? r.text() : null)),
      fetch("/api/docs/SOMA_ACCEPTANCE_CHECKLIST.md").then((r) => (r.ok ? r.text() : null)),
    ])
      .then(([s, c]) => {
        setSpec(s);
        setChecklist(c);
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div>
      <div className="mb-6">
        <Link href="/projects/soma_kajabi" className="text-xs text-blue-400 hover:text-blue-300 mb-2 inline-block">
          ← Soma Project
        </Link>
        <h2 className="text-2xl font-bold text-white/95 tracking-tight">Soma Canonical Docs</h2>
        <p className="text-sm text-white/60 mt-1">Locked spec and acceptance checklist (repo truth)</p>
      </div>

      <div className="flex flex-wrap gap-3 mb-6">
        <a
          href="/api/docs/SOMA_LOCKED_SPEC.md"
          download="SOMA_LOCKED_SPEC.md"
          className="px-3 py-1.5 text-xs font-medium rounded-xl bg-white/10 hover:bg-white/15 text-white/90 border border-white/10"
        >
          Download Locked Spec
        </a>
        <a
          href="/api/docs/SOMA_ACCEPTANCE_CHECKLIST.md"
          download="SOMA_ACCEPTANCE_CHECKLIST.md"
          className="px-3 py-1.5 text-xs font-medium rounded-xl bg-white/10 hover:bg-white/15 text-white/90 border border-white/10"
        >
          Download Acceptance Checklist
        </a>
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

      {!loading && spec && (
        <GlassCard className="mb-6 p-5">
          <h3 className="text-lg font-semibold text-white/95 mb-3">Locked Spec</h3>
          <pre className="whitespace-pre-wrap text-sm text-white/90 leading-relaxed font-mono max-h-[80vh] overflow-auto">
            {spec}
          </pre>
        </GlassCard>
      )}

      {!loading && checklist && (
        <GlassCard className="mb-6 p-5">
          <h3 className="text-lg font-semibold text-white/95 mb-3">Acceptance Checklist</h3>
          <pre className="whitespace-pre-wrap text-sm text-white/90 leading-relaxed font-mono max-h-[80vh] overflow-auto">
            {checklist}
          </pre>
        </GlassCard>
      )}

      {!loading && !spec && !checklist && !error && (
        <p className="text-sm text-white/50">Docs not found. Ensure repo has docs/SOMA_LOCKED_SPEC.md and docs/SOMA_ACCEPTANCE_CHECKLIST.md.</p>
      )}
    </div>
  );
}
