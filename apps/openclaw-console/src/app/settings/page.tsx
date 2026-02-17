"use client";

import { useState } from "react";
import { GlassButton } from "@/components/glass";

export default function SettingsPage() {
  const [copyStatus, setCopyStatus] = useState<"idle" | "copying" | "copied" | "error">("idle");

  const copyDebugInfo = async () => {
    setCopyStatus("copying");
    try {
      const healthRes = await fetch("/api/ui/health");
      const healthData = healthRes.ok ? await healthRes.json() : { error: "health check failed" };

      const debugInfo = [
        `URL: ${window.location.href}`,
        `Build SHA: ${healthData.build_sha || "unknown"}`,
        `Server Time: ${healthData.server_time || "unknown"}`,
        `User Agent: ${navigator.userAgent}`,
        `Artifacts Readable: ${healthData.artifacts?.readable ?? "unknown"}`,
        `Artifact Dirs: ${healthData.artifacts?.dir_count ?? "unknown"}`,
        `Node: ${healthData.node_version || "unknown"}`,
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
      <div className="glass-surface rounded-2xl p-6 mb-6">
        <p className="text-sm text-white/70">
          HQ binds to 127.0.0.1 only. Authentication uses X-OpenClaw-Token when configured. Admin actions require OPENCLAW_ADMIN_TOKEN.
        </p>
      </div>

      <div className="glass-surface rounded-2xl p-6">
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
    </div>
  );
}
