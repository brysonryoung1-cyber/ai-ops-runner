"use client";

import { useState } from "react";
import Link from "next/link";
import { useToken } from "@/lib/token-context";

export interface ForbiddenInfo {
  status: number;
  route: string;
  error?: string;
  error_class?: string;
  message?: string;
  timestamp: string;
}

function diagnoseCause(info: ForbiddenInfo): string {
  const ec = info.error_class ?? "";
  const err = (info.error ?? info.message ?? "").toLowerCase();

  if (ec === "HQ_TOKEN_MISSING" || err.includes("x-openclaw-token")) {
    return "HQ token required (X-OpenClaw-Token missing or invalid)";
  }
  if (ec === "ADMIN_TOKEN_MISSING" || err.includes("admin") || err.includes("deploy+verify")) {
    return "Server missing OPENCLAW_ADMIN_TOKEN (host executor admin actions blocked)";
  }
  if (ec === "MISSING_GMAIL_CLIENT_JSON") {
    return "Gmail OAuth client JSON missing. Upload gmail_client.json in Settings → Connectors → Gmail OAuth, or see /api/connectors/gmail/requirements for redirect URIs.";
  }
  if (err.includes("origin") || err.includes("csrf") || err.includes("forbidden: request origin")) {
    return "Origin/CSRF blocked — request origin could not be verified";
  }
  if (info.status === 403) {
    return info.error || "Forbidden — unknown cause";
  }
  return info.error || `HTTP ${info.status}`;
}

interface ForbiddenBannerProps {
  info: ForbiddenInfo;
  onDismiss?: () => void;
}

export default function ForbiddenBanner({ info, onDismiss }: ForbiddenBannerProps) {
  const token = useToken();
  const [bundleStatus, setBundleStatus] = useState<"idle" | "loading" | "done" | "error">("idle");
  const [bundleLink, setBundleLink] = useState<string | null>(null);

  const cause = diagnoseCause(info);

  const generateBundle = async () => {
    setBundleStatus("loading");
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
  };

  return (
    <div
      data-testid="forbidden-banner"
      className="mb-6 p-4 rounded-2xl glass-surface border border-red-400/30 bg-red-500/10"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold text-red-300">
            403 Forbidden
          </p>
          <p className="text-xs text-red-200/80 mt-1">
            {cause}
          </p>
          <p className="text-[10px] text-white/40 mt-1">
            Route: {info.route} · {new Date(info.timestamp).toLocaleTimeString()}
          </p>
        </div>
        {onDismiss && (
          <button
            type="button"
            onClick={onDismiss}
            className="text-white/40 hover:text-white/70 text-sm leading-none p-1"
            aria-label="Dismiss"
          >
            ×
          </button>
        )}
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        <button
          type="button"
          onClick={generateBundle}
          disabled={bundleStatus === "loading"}
          className="inline-flex items-center px-3 py-1.5 text-xs font-medium rounded-xl border border-red-400/20 bg-red-500/20 text-red-200 hover:bg-red-500/30 disabled:opacity-50 transition-all"
          data-testid="support-bundle-btn"
        >
          {bundleStatus === "idle" && "Generate support bundle"}
          {bundleStatus === "loading" && "Generating…"}
          {bundleStatus === "done" && "Done"}
          {bundleStatus === "error" && "Failed — retry"}
        </button>
        {bundleLink && (
          <Link
            href={bundleLink}
            className="inline-flex items-center px-3 py-1.5 text-xs font-medium rounded-xl border border-white/10 bg-white/10 text-blue-300 hover:bg-white/15 transition-all"
          >
            View bundle →
          </Link>
        )}
        <Link
          href="/settings"
          className="inline-flex items-center px-3 py-1.5 text-xs font-medium rounded-xl border border-white/10 bg-white/5 text-white/70 hover:bg-white/10 transition-all"
        >
          Auth diagnostics
        </Link>
      </div>
    </div>
  );
}
