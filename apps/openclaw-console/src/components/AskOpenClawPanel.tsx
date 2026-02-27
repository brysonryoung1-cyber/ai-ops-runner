"use client";

import { useState, useCallback } from "react";
import Link from "next/link";
import { GlassCard, GlassButton } from "@/components/glass";

const QUICK_PROMPTS = [
  "Are we drifted?",
  "What's broken?",
  "Why is Soma waiting?",
  "Is noVNC reachable?",
  "What changed since last deploy?",
];

interface AskResponse {
  ok: boolean;
  answer?: string;
  citations?: string[];
  recommended_next_action?: { action: string; read_only?: boolean };
  confidence?: string;
  state_pack_run_id?: string;
  error?: string;
  error_class?: string;
}

interface AskOpenClawPanelProps {
  token: string | null;
  statePackRunId?: string | null;
}

export default function AskOpenClawPanel({ token, statePackRunId }: AskOpenClawPanelProps) {
  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(false);
  const [response, setResponse] = useState<AskResponse | null>(null);

  const sendQuestion = useCallback(
    async (q: string) => {
      const toSend = (typeof q === "string" ? q : question).trim();
      if (!toSend) return;
      setLoading(true);
      setResponse(null);
      try {
        const headers: Record<string, string> = { "Content-Type": "application/json" };
        if (token) headers["X-OpenClaw-Token"] = token;
        const res = await fetch("/api/ask", {
          method: "POST",
          headers,
          body: JSON.stringify({ question: toSend }),
        });
        const data = (await res.json()) as AskResponse;
        setResponse(data);
      } catch {
        setResponse({ ok: false, error: "Request failed" });
      } finally {
        setLoading(false);
      }
    },
    [question, token]
  );

  return (
    <GlassCard className="mb-6">
      <div className="px-5 py-3 border-b border-white/10 flex items-center justify-between">
        <span className="text-sm font-semibold text-white/95">Ask OpenClaw</span>
        {statePackRunId && (
          <Link
            href={`/artifacts/system/state_pack/${statePackRunId}`}
            className="text-xs text-blue-300 hover:text-blue-200 font-mono"
          >
            State Pack: {statePackRunId.slice(0, 20)}…
          </Link>
        )}
      </div>
      <div className="p-5 space-y-4">
        <div className="flex gap-2">
          <input
            type="text"
            placeholder="Ask a grounded question…"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && sendQuestion(question)}
            className="flex-1 px-3 py-2 rounded-lg bg-white/5 border border-white/10 text-white placeholder-white/40 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500/50"
            disabled={loading}
          />
          <GlassButton variant="primary" onClick={() => sendQuestion(question)} disabled={loading || !question.trim()}>
            {loading ? "…" : "Send"}
          </GlassButton>
        </div>

        <div className="flex flex-wrap gap-2">
          {QUICK_PROMPTS.map((p) => (
            <button
              key={p}
              onClick={() => {
                setQuestion(p);
                sendQuestion(p);
              }}
              className="text-xs px-2 py-1 rounded bg-white/5 hover:bg-white/10 text-white/80 transition-colors"
              disabled={loading}
            >
              {p}
            </button>
          ))}
        </div>

        {response && (
          <div className="space-y-3 pt-2 border-t border-white/10">
            {response.ok ? (
              <>
                <p className="text-sm text-white/90 whitespace-pre-wrap">{response.answer}</p>
                {response.citations && response.citations.length > 0 && (
                  <div>
                    <p className="text-xs text-white/50 uppercase tracking-wider mb-1">Citations</p>
                    <ul className="space-y-1">
                      {response.citations.map((path) => (
                        <li key={path}>
                          <Link
                            href={`/artifacts/${path.replace(/^artifacts\//, "")}`}
                            className="text-xs text-blue-300 hover:text-blue-200 font-mono break-all"
                          >
                            {path}
                          </Link>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {response.confidence && (
                  <p className="text-xs text-white/50">Confidence: {response.confidence}</p>
                )}
              </>
            ) : (
              <p className="text-sm text-red-300">{response.error ?? "Error"}</p>
            )}
            <span title="Read-only for now">
              <GlassButton variant="secondary" size="sm" disabled>
                Create Plan
              </GlassButton>
            </span>
          </div>
        )}
      </div>
    </GlassCard>
  );
}
