"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { GlassCard, GlassButton } from "@/components/glass";

interface Incident {
  incident_id: string;
  status?: string;
  summary?: string;
  artifact_dir: string;
}

export default function IncidentsPage() {
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchIncidents = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch("/api/incidents?limit=30");
      const data = await res.json();
      if (!res.ok) {
        setError(data.error || `HTTP ${res.status}`);
        setIncidents([]);
        return;
      }
      setIncidents(data.incidents || []);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load");
      setIncidents([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchIncidents();
  }, []);

  return (
    <div>
      <div className="flex items-center justify-between mb-8">
        <div>
          <h2 className="text-2xl font-bold text-white/95 tracking-tight">Incidents</h2>
          <p className="text-sm text-white/60 mt-1">Reconcile failures and remediation runs (self-documenting ledger)</p>
        </div>
        <GlassButton onClick={() => fetchIncidents()} disabled={loading} size="sm">
          {loading ? "Loading…" : "Refresh"}
        </GlassButton>
      </div>

      {error && (
        <div className="p-4 rounded-2xl glass-surface border border-red-500/20 mb-4">
          <p className="text-sm font-semibold text-red-300">Error</p>
          <p className="text-xs text-red-200/80 mt-1">{error}</p>
        </div>
      )}

      {loading && incidents.length === 0 && !error && (
        <div className="glass-surface rounded-2xl p-12 text-center">
          <div className="inline-block w-6 h-6 border-2 border-blue-400 border-t-transparent rounded-full animate-spin" />
          <p className="text-sm text-white/60 mt-3">Loading incidents…</p>
        </div>
      )}

      {!loading && incidents.length === 0 && !error && (
        <GlassCard>
          <div className="p-8 text-center text-white/60">
            <p className="text-sm">No incidents recorded yet.</p>
            <p className="text-xs mt-2">Incidents are created when reconcile fails or playbooks run.</p>
          </div>
        </GlassCard>
      )}

      {!loading && incidents.length > 0 && (
        <GlassCard>
          <div className="px-4 py-2.5 border-b border-white/10 flex items-center justify-between">
            <span className="text-xs text-white/50">{incidents.length} incidents</span>
          </div>
          <ul className="divide-y divide-white/5">
            {incidents.map((inc) => (
              <li key={inc.incident_id} className="hover:bg-white/5 transition-colors">
                <Link
                  href={`/artifacts/incidents/${encodeURIComponent(inc.incident_id)}`}
                  className="flex items-center justify-between px-5 py-3"
                >
                  <div className="flex flex-col gap-1">
                    <span className="text-sm font-mono text-white/90">{inc.incident_id}</span>
                    {inc.status && (
                      <span
                        className={`text-xs font-medium ${
                          inc.status === "REMEDIATION"
                            ? "text-amber-400"
                            : inc.status === "WAITING_FOR_HUMAN" || inc.status === "FAILURE"
                              ? "text-red-400"
                              : "text-white/60"
                        }`}
                      >
                        {inc.status}
                      </span>
                    )}
                    {inc.summary && (
                      <p className="text-xs text-white/50 line-clamp-2">{inc.summary}</p>
                    )}
                  </div>
                  <svg className="w-4 h-4 text-white/40 flex-shrink-0" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
                  </svg>
                </Link>
              </li>
            ))}
          </ul>
        </GlassCard>
      )}
    </div>
  );
}
