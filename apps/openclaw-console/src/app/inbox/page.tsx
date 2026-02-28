"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { GlassCard, GlassButton } from "@/components/glass";

interface WaitingForHuman {
  project_id: string;
  run_id: string;
  reason: string;
  canonical_url: string | null;
  browser_gateway_url: string | null;
  single_instruction: string | null;
  artifacts_link: string | null;
}

interface Degraded {
  subsystem: string;
  run_id: string;
  failing_checks: string[];
  proof_link: string | null;
  incident_link: string | null;
}

interface LastProof {
  tree_sha: string | null;
  run_id: string | null;
  proof_link: string | null;
  timestamp: string | null;
}

interface LastDeploy {
  build_sha: string | null;
  deploy_time: string | null;
  version_link: string | null;
}

interface LastCanary {
  status: string | null;
  run_id: string | null;
  proof_link: string | null;
  timestamp: string | null;
}

interface InboxData {
  waiting_for_human: WaitingForHuman[];
  degraded: Degraded[];
  last_proof: LastProof;
  last_deploy: LastDeploy;
  last_canary: LastCanary;
}

function BrowserGatewayButton({ runId, existingUrl }: { runId: string; existingUrl: string | null }) {
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleClick = async () => {
    if (existingUrl) {
      window.open(existingUrl, "_blank");
      return;
    }
    setStarting(true);
    setError(null);
    try {
      const resp = await fetch("/api/browser-gateway/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ run_id: runId, purpose: "kajabi_login" }),
      });
      const data = await resp.json();
      if (data.ok && data.viewer_url) {
        window.open(data.viewer_url, "_blank");
      } else {
        setError(data.error || "Failed to start");
      }
    } catch {
      setError("Gateway unreachable");
    } finally {
      setStarting(false);
    }
  };

  return (
    <div className="flex flex-col gap-1">
      <button
        onClick={handleClick}
        disabled={starting}
        className="inline-flex items-center px-3 py-1.5 rounded-lg bg-blue-500/20 text-blue-200 text-sm font-medium hover:bg-blue-500/30 transition-colors disabled:opacity-50"
      >
        {starting ? "Starting…" : "Open Browser Gateway"}
      </button>
      {error && <span className="text-[10px] text-red-400">{error}</span>}
    </div>
  );
}

export default function InboxPage() {
  const [data, setData] = useState<InboxData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/operator-inbox")
      .then((r) => r.json())
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load"))
      .finally(() => setLoading(false));
  }, []);

  if (loading && !data) {
    return (
      <div className="glass-surface rounded-2xl p-12 text-center">
        <div className="inline-block w-6 h-6 border-2 border-blue-400 border-t-transparent rounded-full animate-spin" />
        <p className="text-sm text-white/60 mt-3">Loading Operator Inbox…</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-4 rounded-2xl glass-surface border border-red-500/20">
        <p className="text-sm font-semibold text-red-300">Error</p>
        <p className="text-xs text-red-200/80 mt-1">{error}</p>
      </div>
    );
  }

  const inbox = data ?? {
    waiting_for_human: [],
    degraded: [],
    last_proof: { tree_sha: null, run_id: null, proof_link: null, timestamp: null },
    last_deploy: { build_sha: null, deploy_time: null, version_link: null },
    last_canary: { status: null, run_id: null, proof_link: null, timestamp: null },
  };

  return (
    <div data-testid="operator-inbox-page">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h2 className="text-2xl font-bold text-white/95 tracking-tight">Operator Inbox</h2>
          <p className="text-sm text-white/60 mt-1">
            Actionable items: human gates, degraded canaries, last proof/deploy
          </p>
        </div>
        <GlassButton
          onClick={() => {
            setLoading(true);
            fetch("/api/operator-inbox")
              .then((r) => r.json())
              .then(setData)
              .finally(() => setLoading(false));
          }}
          disabled={loading}
          size="sm"
        >
          {loading ? "Refreshing…" : "Refresh"}
        </GlassButton>
      </div>

      {/* 1) Needs You (WAITING_FOR_HUMAN) */}
      <section className="mb-8">
        <h3 className="text-lg font-semibold text-white/90 mb-3">Needs You</h3>
        {inbox.waiting_for_human.length === 0 ? (
          <GlassCard>
            <div className="p-6 text-center text-white/50 text-sm">No human gates waiting</div>
          </GlassCard>
        ) : (
          <ul className="space-y-3">
            {inbox.waiting_for_human.map((item) => (
              <li key={`${item.project_id}-${item.run_id}`}>
                <GlassCard>
                  <div className="p-4 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
                    <div>
                      <span className="text-sm font-mono text-white/90">{item.project_id}</span>
                      <span className="text-white/40 mx-2">·</span>
                      <span className="text-xs text-white/60">{item.run_id}</span>
                      <p className="text-xs text-amber-300/90 mt-1">{item.reason}</p>
                      {item.single_instruction && (
                        <p className="text-xs text-white/70 mt-1">{item.single_instruction}</p>
                      )}
                    </div>
                    <div className="flex gap-2 flex-shrink-0 flex-wrap">
                      <BrowserGatewayButton runId={item.run_id} existingUrl={item.browser_gateway_url} />
                      {item.canonical_url ? (
                        <details className="inline-flex">
                          <summary className="cursor-pointer inline-flex items-center px-3 py-1.5 rounded-lg bg-white/5 text-white/50 text-xs font-medium hover:bg-white/10 transition-colors list-none">
                            Advanced
                          </summary>
                          <div className="mt-1">
                            <a
                              href={item.canonical_url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="inline-flex items-center px-3 py-1.5 rounded-lg bg-amber-500/10 text-amber-200/70 text-xs font-medium hover:bg-amber-500/20 transition-colors"
                            >
                              Open noVNC (fallback)
                            </a>
                          </div>
                        </details>
                      ) : null}
                      {item.artifacts_link && (
                        <Link
                          href={item.artifacts_link}
                          className="inline-flex items-center px-3 py-1.5 rounded-lg bg-white/10 text-white/90 text-sm font-medium hover:bg-white/15 transition-colors"
                        >
                          Open run
                        </Link>
                      )}
                    </div>
                  </div>
                </GlassCard>
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* 2) Degraded (Canary/Reconcile failures) */}
      <section className="mb-8">
        <h3 className="text-lg font-semibold text-white/90 mb-3">Degraded</h3>
        {inbox.degraded.length === 0 ? (
          <GlassCard>
            <div className="p-6 text-center text-white/50 text-sm">No degraded subsystems</div>
          </GlassCard>
        ) : (
          <ul className="space-y-3">
            {inbox.degraded.map((item) => (
              <li key={`${item.subsystem}-${item.run_id}`}>
                <GlassCard>
                  <div className="p-4 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
                    <div>
                      <span className="text-sm font-mono text-white/90">{item.subsystem}</span>
                      <span className="text-white/40 mx-2">·</span>
                      <span className="text-xs text-white/60">{item.run_id}</span>
                      {item.failing_checks.length > 0 && (
                        <p className="text-xs text-red-300/90 mt-1">
                          {item.failing_checks.join(", ")}
                        </p>
                      )}
                    </div>
                    <div className="flex gap-2 flex-shrink-0">
                      {item.proof_link && (
                        <Link
                          href={item.proof_link}
                          className="inline-flex items-center px-3 py-1.5 rounded-lg bg-blue-500/20 text-blue-200 text-sm font-medium hover:bg-blue-500/30 transition-colors"
                        >
                          Open proof
                        </Link>
                      )}
                      {item.incident_link && (
                        <Link
                          href={item.incident_link}
                          className="inline-flex items-center px-3 py-1.5 rounded-lg bg-white/10 text-white/90 text-sm font-medium hover:bg-white/15 transition-colors"
                        >
                          Open incident
                        </Link>
                      )}
                    </div>
                  </div>
                </GlassCard>
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* 3) Recent (Last deploy/proof/canary) */}
      <section>
        <h3 className="text-lg font-semibold text-white/90 mb-3">Recent</h3>
        <GlassCard>
          <div className="divide-y divide-white/5">
            {inbox.last_proof.run_id && (
              <div className="p-4 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
                <div>
                  <span className="text-sm font-medium text-white/90">Last proof</span>
                  <span className="text-white/40 mx-2">·</span>
                  <span className="text-xs font-mono text-white/60">{inbox.last_proof.run_id}</span>
                </div>
                {inbox.last_proof.proof_link && (
                  <Link
                    href={inbox.last_proof.proof_link}
                    className="inline-flex items-center px-3 py-1.5 rounded-lg bg-green-500/20 text-green-200 text-sm font-medium hover:bg-green-500/30 transition-colors w-fit"
                  >
                    Open proof
                  </Link>
                )}
              </div>
            )}
            {inbox.last_deploy.version_link && (
              <div className="p-4 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
                <div>
                  <span className="text-sm font-medium text-white/90">Last deploy</span>
                  {inbox.last_deploy.build_sha && (
                    <>
                      <span className="text-white/40 mx-2">·</span>
                      <span className="text-xs font-mono text-white/60">
                        {inbox.last_deploy.build_sha}
                      </span>
                    </>
                  )}
                  {inbox.last_deploy.deploy_time && (
                    <p className="text-xs text-white/50 mt-1">{inbox.last_deploy.deploy_time}</p>
                  )}
                </div>
                <Link
                  href={inbox.last_deploy.version_link}
                  className="inline-flex items-center px-3 py-1.5 rounded-lg bg-white/10 text-white/90 text-sm font-medium hover:bg-white/15 transition-colors w-fit"
                >
                  Open deploy
                </Link>
              </div>
            )}
            {inbox.last_canary.run_id && (
              <div className="p-4 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
                <div>
                  <span className="text-sm font-medium text-white/90">Last canary</span>
                  <span className="text-white/40 mx-2">·</span>
                  <span className="text-xs font-mono text-white/60">{inbox.last_canary.run_id}</span>
                  {inbox.last_canary.status && (
                    <span
                      className={`ml-2 text-xs font-medium ${
                        inbox.last_canary.status === "PASS"
                          ? "text-green-400"
                          : "text-amber-400"
                      }`}
                    >
                      {inbox.last_canary.status}
                    </span>
                  )}
                </div>
                {inbox.last_canary.proof_link && (
                  <Link
                    href={inbox.last_canary.proof_link}
                    className="inline-flex items-center px-3 py-1.5 rounded-lg bg-green-500/20 text-green-200 text-sm font-medium hover:bg-green-500/30 transition-colors w-fit"
                  >
                    Open proof
                  </Link>
                )}
              </div>
            )}
            {!inbox.last_proof.run_id &&
              !inbox.last_deploy.version_link &&
              !inbox.last_canary.run_id && (
                <div className="p-6 text-center text-white/50 text-sm">No recent activity</div>
              )}
          </div>
        </GlassCard>
      </section>
    </div>
  );
}
