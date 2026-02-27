"use client";

import { useState, useEffect, useCallback } from "react";
import Link from "next/link";
import { useToken } from "@/lib/token-context";
import { GlassButton } from "@/components/glass";
import { toCanonicalNovncUrl } from "@/lib/novnc-url";

interface SomaStatus {
  current_status: string | null;
  last_status: string | null;
  novnc_url: string | null;
  instruction_line: string | null;
  artifact_dir: string | null;
  active_run_id: string | null;
  resume_action_available: boolean;
  artifact_links?: Record<string, string>;
  framebuffer_url: string | null;
  artifact_dir_url: string | null;
  doctor_framebuffer_url: string | null;
  doctor_artifact_dir_url: string | null;
}

const POLL_INTERVAL_MS = 30_000;
const SESSION_CHECK_ACTION = "soma_kajabi_session_check";

export default function GuidedHumanGateBanner() {
  const token = useToken();
  const [status, setStatus] = useState<SomaStatus | null>(null);
  const [embedNovnc, setEmbedNovnc] = useState(false);
  const [resumeLoading, setResumeLoading] = useState(false);
  const [resumeResult, setResumeResult] = useState<{ ok: boolean; message?: string } | null>(null);
  const [novncModalOpen, setNovncModalOpen] = useState(false);

  const fetchStatus = useCallback(async () => {
    try {
      const headers: Record<string, string> = {};
      if (token) headers["X-OpenClaw-Token"] = token;
      const [statusRes, healthRes] = await Promise.all([
        fetch("/api/projects/soma_kajabi/status", { headers }),
        fetch("/api/ui/health_public"),
      ]);
      const statusData = await statusRes.json();
      const healthData = await healthRes.json();
      if (statusData?.ok) {
        const raw = statusData.novnc_url ?? statusData.novnc_url_canonical ?? null;
        const canonical = toCanonicalNovncUrl(raw) ?? raw;
        setStatus({
          current_status: statusData.current_status ?? statusData.last_status ?? null,
          last_status: statusData.last_status ?? null,
          novnc_url: canonical ?? null,
          instruction_line: statusData.instruction_line ?? null,
          artifact_dir: statusData.artifact_dir ?? null,
          active_run_id: statusData.active_run_id ?? statusData.last_run_id ?? null,
          resume_action_available: statusData.resume_action_available === true,
          artifact_links: statusData.artifact_links ?? {},
          framebuffer_url: statusData.framebuffer_url ?? null,
          artifact_dir_url: statusData.artifact_dir_url ?? null,
          doctor_framebuffer_url: statusData.doctor_framebuffer_url ?? null,
          doctor_artifact_dir_url: statusData.doctor_artifact_dir_url ?? null,
        });
      } else {
        setStatus(null);
      }
      setEmbedNovnc(healthData?.embed_novnc === true);
    } catch {
      setStatus(null);
    }
  }, [token]);

  useEffect(() => {
    fetchStatus();
    const id = setInterval(fetchStatus, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [fetchStatus]);

  const handleResume = useCallback(async () => {
    setResumeLoading(true);
    setResumeResult(null);
    try {
      const headers: Record<string, string> = { "Content-Type": "application/json" };
      if (token) headers["X-OpenClaw-Token"] = token;
      const res = await fetch("/api/exec", {
        method: "POST",
        headers,
        body: JSON.stringify({ action: SESSION_CHECK_ACTION }),
      });
      const data = await res.json();
      const ok = data?.ok === true;
      setResumeResult({
        ok,
        message: ok ? "PASS — Autopilot will resume" : data?.error ?? data?.error_class ?? "Check failed",
      });
      if (ok) fetchStatus();
    } catch (err) {
      setResumeResult({ ok: false, message: err instanceof Error ? err.message : "Request failed" });
    } finally {
      setResumeLoading(false);
    }
  }, [token, fetchStatus]);

  const handleOpenNovnc = useCallback(() => {
    if (!status?.novnc_url) return;
    if (embedNovnc) {
      setNovncModalOpen(true);
    } else {
      window.open(status.novnc_url!, "_blank", "noopener,noreferrer");
    }
  }, [status?.novnc_url, embedNovnc]);

  const framebufferUrl = status?.framebuffer_url ?? status?.doctor_framebuffer_url ?? null;
  const framebufferIsDoctorFallback = !status?.framebuffer_url && !!status?.doctor_framebuffer_url;
  const artifactDirUrl = status?.artifact_dir_url ?? status?.doctor_artifact_dir_url ?? null;

  const handleCopyLinks = useCallback(() => {
    const origin = typeof window !== "undefined" ? window.location.origin : "";
    const lines: string[] = [];
    if (status?.novnc_url) lines.push(`noVNC (primary): ${status.novnc_url}`);
    if (status?.framebuffer_url) lines.push(`framebuffer: ${origin}${status.framebuffer_url}`);
    if (status?.doctor_framebuffer_url) lines.push(`framebuffer (doctor fallback): ${origin}${status.doctor_framebuffer_url}`);
    if (status?.artifact_dir_url) lines.push(`artifact_dir: ${origin}${status.artifact_dir_url}`);
    if (status?.doctor_artifact_dir_url) lines.push(`artifact_dir (doctor fallback): ${origin}${status.doctor_artifact_dir_url}`);
    const text = lines.length > 0 ? lines.join("\n") : "No links available";
    navigator.clipboard.writeText(text);
  }, [status]);

  if (!status || status.current_status !== "WAITING_FOR_HUMAN") return null;

  return (
    <>
      <div
        className="mb-6 p-5 rounded-2xl glass-surface border border-amber-500/40 bg-amber-500/5"
        data-testid="guided-human-gate-banner"
      >
        <h3 className="text-base font-semibold text-amber-200 mb-2">Soma needs you: Kajabi login</h3>
        <p className="text-sm text-white/80 mb-2">
          After completing 2FA, stop touching the session. Autopilot will resume.
        </p>
        <p className="text-xs text-white/50 mb-4">Polling every 10 min · Click Resume after login</p>
        <div className="flex flex-wrap gap-3">
          {status.novnc_url && (
            <button
              type="button"
              onClick={handleOpenNovnc}
              className="px-4 py-2 rounded-xl bg-amber-500/20 hover:bg-amber-500/30 text-amber-200 font-medium text-sm border border-amber-500/30"
            >
              Open noVNC
            </button>
          )}
          <GlassButton
            variant="secondary"
            size="sm"
            onClick={handleResume}
            disabled={resumeLoading || !status.resume_action_available}
          >
            {resumeLoading ? "Checking…" : "Resume after login"}
          </GlassButton>
          {framebufferUrl ? (
            <Link
              href={framebufferUrl}
              className="px-4 py-2 rounded-xl bg-white/10 hover:bg-white/15 text-white/90 font-medium text-sm border border-white/20"
            >
              Open framebuffer{framebufferIsDoctorFallback ? " (doctor fallback)" : ""}
            </Link>
          ) : (
            <span
              className="px-4 py-2 rounded-xl bg-white/5 text-white/40 font-medium text-sm border border-white/10 cursor-not-allowed"
              title="No framebuffer available yet"
            >
              Open framebuffer
            </span>
          )}
          {artifactDirUrl ? (
            <Link
              href={artifactDirUrl}
              className="px-4 py-2 rounded-xl bg-white/10 hover:bg-white/15 text-white/90 font-medium text-sm border border-white/20"
            >
              Open artifacts
            </Link>
          ) : (
            <span
              className="px-4 py-2 rounded-xl bg-white/5 text-white/40 font-medium text-sm border border-white/10 cursor-not-allowed"
              title="No artifact directory available"
            >
              Open artifacts
            </span>
          )}
          {(status?.novnc_url || framebufferUrl || artifactDirUrl) && (
            <GlassButton variant="secondary" size="sm" onClick={handleCopyLinks}>
              Copy links
            </GlassButton>
          )}
          {status.instruction_line && (
            <GlassButton
              variant="secondary"
              size="sm"
              onClick={() => navigator.clipboard.writeText(status.instruction_line ?? "")}
            >
              Copy instructions
            </GlassButton>
          )}
          {status.active_run_id && (
            <Link
              href={`/runs?id=${encodeURIComponent(status.active_run_id)}`}
              className="px-4 py-2 rounded-xl bg-white/10 hover:bg-white/15 text-white/90 font-medium text-sm border border-white/20"
            >
              Open active run →
            </Link>
          )}
        </div>
        {resumeResult && (
          <p
            className={`mt-3 text-sm ${resumeResult.ok ? "text-emerald-300" : "text-amber-300"}`}
          >
            {resumeResult.message}
          </p>
        )}
      </div>

      {/* noVNC modal (OPENCLAW_EMBED_NOVNC=1). Fallback: open in new tab if iframe blocked. */}
      {embedNovnc && novncModalOpen && status?.novnc_url && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm"
          role="dialog"
          aria-modal="true"
          aria-label="noVNC session"
        >
          <div className="relative w-[95vw] h-[90vh] max-w-6xl max-h-[85vh] rounded-2xl overflow-hidden bg-black border border-white/20">
            <div className="absolute top-3 right-3 z-10 flex gap-2">
              <a
                href={status.novnc_url}
                target="_blank"
                rel="noopener noreferrer"
                className="p-2 rounded-lg bg-white/10 hover:bg-white/20 text-white font-medium text-sm"
              >
                Open in new tab
              </a>
              <button
                type="button"
                onClick={() => setNovncModalOpen(false)}
                className="p-2 rounded-lg bg-white/10 hover:bg-white/20 text-white font-medium text-sm"
                aria-label="Close"
              >
                Close
              </button>
            </div>
            <iframe
              src={status.novnc_url}
              title="noVNC session"
              className="w-full h-full border-0"
              sandbox="allow-scripts allow-same-origin"
            />
          </div>
        </div>
      )}
    </>
  );
}
