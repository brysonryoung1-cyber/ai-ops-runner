"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { GlassButton } from "@/components/glass";
import { useToken } from "@/lib/token-context";

interface AuthStatus {
  ok: boolean;
  hq_token_required: boolean;
  admin_token_loaded: boolean;
  host_executor_reachable: boolean;
  build_sha: string;
  trust_tailscale: boolean;
  console_token_fingerprint: string | null;
  notes: string[];
}

function StatusBadge({ ok, label }: { ok: boolean; label: string }) {
  return (
    <div className="flex items-center gap-2">
      <span
        className={`w-2 h-2 rounded-full ${ok ? "bg-green-400" : "bg-red-400"}`}
      />
      <span className="text-xs text-white/80">{label}</span>
      <span className={`text-xs font-medium ${ok ? "text-green-400" : "text-red-400"}`}>
        {ok ? "Yes" : "No"}
      </span>
    </div>
  );
}

export default function SettingsPage() {
  const token = useToken();
  const [copyStatus, setCopyStatus] = useState<"idle" | "copying" | "copied" | "error">("idle");
  const [bundleStatus, setBundleStatus] = useState<"idle" | "loading" | "done" | "error">("idle");
  const [bundleLink, setBundleLink] = useState<string | null>(null);
  const [authStatus, setAuthStatus] = useState<AuthStatus | null>(null);
  const [authError, setAuthError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/auth/status")
      .then((r) => r.json())
      .then((data) => setAuthStatus(data))
      .catch((e) => setAuthError(e instanceof Error ? e.message : String(e)));
  }, []);

  const copyDebugInfo = async () => {
    setCopyStatus("copying");
    try {
      const healthRes = await fetch("/api/ui/health_public");
      const healthData = healthRes.ok ? await healthRes.json() : { error: "health check failed" };

      const debugInfo = [
        `URL: ${window.location.href}`,
        `Build SHA: ${healthData.build_sha || "unknown"}`,
        `Server Time: ${healthData.server_time || "unknown"}`,
        `User Agent: ${navigator.userAgent}`,
        `Artifacts Readable: ${healthData.artifacts?.readable ?? "unknown"}`,
        `Artifact Dirs: ${healthData.artifacts?.dir_count ?? "unknown"}`,
        "",
        "Routes:",
        ...(healthData.routes || []).map((r: string) => `  ${r}`),
        "",
        `Health JSON: ${JSON.stringify(healthData, null, 2)}`,
      ].join("\n");

      await navigator.clipboard.writeText(debugInfo);
      setCopyStatus("copied");
      setTimeout(() => setCopyStatus("idle"), 3000);
    } catch {
      setCopyStatus("error");
      setTimeout(() => setCopyStatus("idle"), 3000);
    }
  };

  return (
    <div>
      <div className="mb-8">
        <h2 className="text-2xl font-bold text-white/95 tracking-tight">
          Settings
        </h2>
        <p className="text-sm text-white/60 mt-1">
          OpenClaw HQ configuration
        </p>
      </div>

      {/* Auth Status Panel */}
      <div data-testid="auth-status-panel" className="glass-surface rounded-2xl p-6 mb-6">
        <h3 className="text-sm font-semibold text-white/95 mb-3">Auth & System Status</h3>
        {authError && (
          <p className="text-xs text-red-400 mb-3">Failed to load auth status: {authError}</p>
        )}
        {authStatus && (
          <div className="space-y-3">
            <div className="flex items-center gap-3 mb-2">
              <span data-testid="build-sha" className="text-xs text-white/50 font-mono bg-white/5 px-2 py-1 rounded">
                Build: {authStatus.build_sha}
              </span>
              {authStatus.trust_tailscale && (
                <span className="text-[10px] text-blue-300 bg-blue-500/10 px-2 py-0.5 rounded-full border border-blue-400/20">
                  Tailscale Trusted
                </span>
              )}
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              <StatusBadge ok={authStatus.hq_token_required} label="HQ Token Required" />
              <StatusBadge ok={authStatus.admin_token_loaded} label="Admin Token Loaded" />
              <StatusBadge ok={authStatus.host_executor_reachable} label="Host Executor Reachable" />
            </div>
            {authStatus.console_token_fingerprint && (
              <p className="text-[10px] text-white/40 mt-2">
                Token fingerprint: {authStatus.console_token_fingerprint}
              </p>
            )}
            {authStatus.notes.length > 0 && (
              <div className="mt-3 border-t border-white/10 pt-3">
                <p className="text-[10px] font-semibold text-white/50 mb-1 uppercase tracking-wider">Notes</p>
                <ul className="space-y-1">
                  {authStatus.notes.map((note, i) => (
                    <li key={i} className="text-xs text-white/60">{note}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
        {!authStatus && !authError && (
          <p className="text-xs text-white/50">Loading auth status…</p>
        )}
      </div>

      <div className="glass-surface rounded-2xl p-6 mb-6">
        <p className="text-sm text-white/70">
          HQ binds to 127.0.0.1 only. Authentication uses X-OpenClaw-Token when configured. Admin actions require OPENCLAW_ADMIN_TOKEN.
        </p>
      </div>

      <div className="glass-surface rounded-2xl p-6 mb-6">
        <h3 className="text-sm font-semibold text-white/95 mb-3">Diagnostics</h3>
        <p className="text-xs text-white/60 mb-4">
          Copy debug information to clipboard for troubleshooting. Includes build SHA, route map, and artifact health. No secrets are included.
        </p>
        <GlassButton onClick={copyDebugInfo} disabled={copyStatus === "copying"} size="sm">
          {copyStatus === "idle" && "Copy UI debug"}
          {copyStatus === "copying" && "Copying…"}
          {copyStatus === "copied" && "Copied!"}
          {copyStatus === "error" && "Copy failed"}
        </GlassButton>
      </div>

      <div id="support-bundle" className="glass-surface rounded-2xl p-6">
        <h3 className="text-sm font-semibold text-white/95 mb-3">Support Bundle</h3>
        <p className="text-xs text-white/60 mb-4">
          Generate a one-click support bundle with ui health, DoD, failing runs, docker status, guard/hostd journals. Stored in artifacts/support_bundle/.
        </p>
        <GlassButton
          onClick={async () => {
            setBundleStatus("loading");
            setBundleLink(null);
            try {
              const headers: Record<string, string> = {};
              if (token) headers["X-OpenClaw-Token"] = token;
              const res = await fetch("/api/support/bundle", { method: "POST", headers });
              const data = await res.json();
              if (data.ok && data.permalink) {
                setBundleLink(data.permalink);
                setBundleStatus("done");
              } else {
                setBundleStatus("error");
              }
            } catch {
              setBundleStatus("error");
            }
          }}
          disabled={bundleStatus === "loading"}
          size="sm"
        >
          {bundleStatus === "idle" && "Generate Support Bundle"}
          {bundleStatus === "loading" && "Generating…"}
          {bundleStatus === "done" && "Done"}
          {bundleStatus === "error" && "Failed"}
        </GlassButton>
        {bundleLink && (
          <Link
            href={bundleLink}
            className="mt-3 inline-block text-xs text-blue-400 hover:text-blue-300"
          >
            View bundle →
          </Link>
        )}
      </div>
    </div>
  );
}
