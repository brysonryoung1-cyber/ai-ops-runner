"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTarget } from "@/lib/target-context";
import StatusDot from "./glass/StatusDot";
import HydrationBadge from "./HydrationBadge";
import GuidedHumanGateBanner from "./GuidedHumanGateBanner";
import NotificationBanner from "./NotificationBanner";

const NAV_ITEMS = [
  { href: "/", label: "Overview" },
  { href: "/projects", label: "Projects" },
  { href: "/runs", label: "Runs" },
  { href: "/artifacts", label: "Artifacts" },
  { href: "/actions", label: "Actions" },
  { href: "/settings", label: "Settings" },
];

export default function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const target = useTarget();
  const [navOpen, setNavOpen] = useState(false);
  const [buildSha, setBuildSha] = useState<string | null>(null);
  const [deploySha, setDeploySha] = useState<string | null>(null);
  const [canonicalUrl, setCanonicalUrl] = useState<string | null>(null);
  const [versionDrift, setVersionDrift] = useState<boolean | null>(null);
  const [versionJson, setVersionJson] = useState<Record<string, unknown> | null>(null);

  useEffect(() => {
    fetch("/api/ui/health_public")
      .then((r) => r.json())
      .then((d) => {
        if (d.build_sha) setBuildSha(d.build_sha);
        if (d.deploy_sha) setDeploySha(d.deploy_sha);
        if (d.canonical_url) setCanonicalUrl(d.canonical_url);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    fetch("/api/ui/version")
      .then((r) => r.json())
      .then((d) => {
        setVersionDrift(d.drift === true || d.drift_status === "unknown");
        setVersionJson(d);
      })
      .catch(() => {});
  }, []);

  const isCanonicalMismatch = (() => {
    if (!canonicalUrl || typeof window === "undefined") return false;
    try {
      return !window.location.origin.includes(new URL(canonicalUrl).hostname);
    } catch {
      return false;
    }
  })();

  return (
    <div className="min-h-screen flex flex-col">
      {/* Top bar (full width) */}
      <header className="flex-shrink-0 glass-surface-strong border-b border-white/10 px-4 py-3 flex items-center justify-between md:justify-start md:gap-6">
        <button
          type="button"
          onClick={() => setNavOpen(!navOpen)}
          className="md:hidden p-2 rounded-lg hover:bg-white/10 transition-colors"
          aria-label="Toggle navigation"
        >
          <svg className="w-5 h-5 text-white/90" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 6.75h16.5M3.75 12h16.5m-16.5 5.25h16.5" />
          </svg>
        </button>
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-xl bg-gradient-to-br from-blue-500/80 to-blue-600/80 flex items-center justify-center backdrop-blur-md">
            <svg className="w-4.5 h-4.5 text-white" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75m-3-7.036A11.959 11.959 0 013.598 6 11.99 11.99 0 003 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285z" />
            </svg>
          </div>
          <div>
            <h1 className="text-sm font-semibold text-white/95">OpenClaw HQ</h1>
            <p className="text-[10px] text-white/50">Control Center</p>
          </div>
        </div>
        {target && (
          <span className="hidden sm:inline-flex items-center px-2.5 py-1 rounded-lg bg-white/10 text-[10px] font-medium text-white/80 border border-white/10">
            {target.name}
          </span>
        )}
        <div className="hidden md:flex items-center gap-2 ml-auto">
          {versionDrift !== null && (
            <a
              href="/api/ui/version"
              target="_blank"
              rel="noopener noreferrer"
              className={`px-2 py-0.5 rounded text-[10px] font-medium ${
                versionDrift
                  ? "bg-amber-500/20 text-amber-300 hover:bg-amber-500/30"
                  : "bg-green-500/20 text-green-300 hover:bg-green-500/30"
              }`}
              title={versionJson ? JSON.stringify(versionJson, null, 2) : undefined}
            >
              {versionDrift ? "DRIFT" : "Up to date"}
            </a>
          )}
          {buildSha && (
            <span className="text-[10px] font-mono text-white/40" title={`Build: ${buildSha}${deploySha ? ` Deploy: ${deploySha}` : ""}`}>
              {buildSha}
            </span>
          )}
          <HydrationBadge />
          <span className="text-white/20 mx-1">|</span>
          <StatusDot variant="idle" />
          <span className="text-[10px] text-white/50">Doctor</span>
          <StatusDot variant="idle" />
          <span className="text-[10px] text-white/50">Guard</span>
          <StatusDot variant="idle" />
          <span className="text-[10px] text-white/50">LLM</span>
        </div>
      </header>

      {/* Content row: sidebar + main */}
      <div className="flex flex-1 min-h-0">
        {/* Nav (desktop sidebar / mobile drawer) */}
        <aside
          className={`
            fixed md:static inset-y-0 left-0 z-40 w-56 min-h-screen md:min-h-0
            glass-surface-strong border-r border-white/10 flex flex-col
            transform transition-transform duration-200 ease-out
            md:transform-none pt-16 md:pt-0
            ${navOpen ? "translate-x-0" : "-translate-x-full md:translate-x-0"}
          `}
        >
        <nav className="flex-1 px-3 py-4" aria-label="Main navigation">
          <ul className="space-y-0.5">
            {NAV_ITEMS.map((item) => {
              const active = pathname === item.href || (item.href !== "/" && pathname.startsWith(item.href));
              return (
                <li key={item.href}>
                  <Link
                    href={item.href}
                    onClick={() => setNavOpen(false)}
                    aria-current={active ? "page" : undefined}
                    className={`flex items-center gap-3 px-3 py-2.5 rounded-xl text-[13px] font-medium transition-all duration-150
                      ${active ? "bg-white/15 text-white" : "text-white/70 hover:bg-white/10 hover:text-white/90"}
                    `}
                  >
                    {item.label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>
          <div className="mt-auto px-4 py-4 border-t border-white/10 space-y-1">
            <p className="text-[11px] text-white/50">Host Executor · 127.0.0.1</p>
            {buildSha && (
              <p data-testid="sidebar-build-sha" className="text-[10px] text-white/30 font-mono truncate" title={`Build: ${buildSha} Deploy: ${deploySha ?? "—"}`}>
                b:{buildSha}{deploySha ? ` d:${deploySha}` : ""}
              </p>
            )}
          </div>
        </aside>

          {/* Overlay when nav open on mobile */}
        {navOpen && (
          <button
            type="button"
            aria-label="Close menu"
            className="fixed inset-0 bg-black/40 z-30 md:hidden"
            onClick={() => setNavOpen(false)}
          />
        )}

        {/* Main content */}
        <main className="flex-1 overflow-auto min-w-0">
          {isCanonicalMismatch && canonicalUrl && (
            <div className="bg-amber-500/10 border-b border-amber-500/20 px-4 py-2 flex items-center justify-between flex-wrap gap-2">
              <p className="text-xs text-amber-300">
                You&apos;re viewing a non-canonical host. The canonical URL is{" "}
                <a href={canonicalUrl} className="font-medium underline hover:text-amber-200">{canonicalUrl}</a>.
              </p>
              <a
                href={canonicalUrl}
                className="inline-flex items-center px-3 py-1 text-xs font-medium rounded-lg bg-amber-500/20 text-amber-200 hover:bg-amber-500/30 transition-colors"
              >
                Go to canonical →
              </a>
            </div>
          )}
          <div className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 py-6 md:py-8">
            <NotificationBanner />
            <GuidedHumanGateBanner />
            {children}
          </div>
        </main>
      </div>
    </div>
  );
}
