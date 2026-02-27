"use client";

import { useState } from "react";
import { GlassCard, StatusDot, Pill } from "@/components/glass";

export default function CodeAgentPage() {
  const [goal, setGoal] = useState("");
  const [ref, setRef] = useState("origin/main");
  const [testCommand, setTestCommand] = useState("");
  const [dryRun, setDryRun] = useState(true);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<{
    ok: boolean;
    run_id?: string;
    status?: string;
    error?: string;
    error_class?: string;
    artifact_dir?: string;
  } | null>(null);

  const handleRun = async () => {
    if (!goal.trim()) {
      setResult({ ok: false, error: "Goal is required" });
      return;
    }
    setLoading(true);
    setResult(null);
    try {
      const res = await fetch("/api/exec", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: "code.opencode.propose_patch",
          params: {
            goal: goal.trim(),
            ref: ref.trim() || "origin/main",
            test_command: testCommand.trim() || undefined,
            dry_run: dryRun,
          },
        }),
      });
      const data = await res.json();
      setResult({
        ok: data.ok === true,
        run_id: data.run_id,
        status: data.status,
        error: data.error,
        error_class: data.error_class,
        artifact_dir: data.artifact_dir,
      });
    } catch (err) {
      setResult({
        ok: false,
        error: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div>
      <div className="mb-8">
        <h2 className="text-2xl font-bold text-white/95 tracking-tight">
          Code Agent (OpenCode)
        </h2>
        <p className="text-sm text-white/60 mt-1">
          Optional patch generator. Proposes patches via OpenCode in a sandboxed container.
        </p>
      </div>

      {/* Safety banner */}
      <div className="mb-6 p-4 rounded-xl bg-amber-500/15 border border-amber-500/30">
        <p className="text-sm font-medium text-amber-200">
          Patch only; does not merge or deploy. Use ship_deploy_verify after approval.
        </p>
      </div>

      <GlassCard className="mb-6">
        <div className="px-5 py-4 space-y-4">
          <div>
            <label className="block text-xs font-medium text-white/70 mb-1.5">
              Goal (required)
            </label>
            <textarea
              value={goal}
              onChange={(e) => setGoal(e.target.value)}
              placeholder="e.g. Add a unit test for the parse_config function"
              className="w-full px-3 py-2 rounded-lg bg-white/5 border border-white/10 text-white placeholder-white/40 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500/50"
              rows={3}
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-white/70 mb-1.5">
              Base ref (default: origin/main)
            </label>
            <input
              type="text"
              value={ref}
              onChange={(e) => setRef(e.target.value)}
              placeholder="origin/main"
              className="w-full px-3 py-2 rounded-lg bg-white/5 border border-white/10 text-white placeholder-white/40 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500/50"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-white/70 mb-1.5">
              Test command (optional)
            </label>
            <input
              type="text"
              value={testCommand}
              onChange={(e) => setTestCommand(e.target.value)}
              placeholder="e.g. pytest tests/"
              className="w-full px-3 py-2 rounded-lg bg-white/5 border border-white/10 text-white placeholder-white/40 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500/50"
            />
          </div>
          <div className="flex items-center gap-2">
            <input
              type="checkbox"
              id="dry_run"
              checked={dryRun}
              onChange={(e) => setDryRun(e.target.checked)}
              className="rounded border-white/20 bg-white/5"
            />
            <label htmlFor="dry_run" className="text-sm text-white/80">
              Dry run (no provider; validates artifact structure)
            </label>
          </div>
          <button
            onClick={handleRun}
            disabled={loading}
            className="px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white text-sm font-medium"
          >
            {loading ? "Running…" : "Run propose_patch"}
          </button>
        </div>
      </GlassCard>

      {result && (
        <GlassCard>
          <div className="px-5 py-3 border-b border-white/10 flex items-center justify-between">
            <div className="flex items-center gap-3">
              <StatusDot variant={result.ok ? "pass" : "fail"} />
              <span className="text-sm font-semibold text-white/95">
                code.opencode.propose_patch
              </span>
              <Pill variant={result.ok ? "success" : "fail"}>
                {result.status === "running" ? "Running" : result.ok ? "Success" : "Error"}
              </Pill>
            </div>
          </div>
          {result.run_id && (
            <div className="px-5 py-3 space-y-2">
              <p className="text-xs text-white/70">
                Run ID: <code className="bg-white/10 px-1 rounded">{result.run_id}</code>
              </p>
              <div className="flex flex-wrap gap-2">
                <a
                  href={`/runs?id=${encodeURIComponent(result.run_id)}`}
                  className="text-xs text-blue-400 hover:text-blue-300 underline"
                >
                  View run →
                </a>
                {result.artifact_dir && (
                  <a
                    href={`/artifacts/${result.artifact_dir.replace(/^artifacts\/?/, "")}`}
                    className="text-xs text-blue-400 hover:text-blue-300 underline"
                  >
                    View artifacts (patch.diff, PROOF.md) →
                  </a>
                )}
              </div>
            </div>
          )}
          {result.error && (
            <div className="px-5 py-3 bg-red-500/10 border-t border-red-500/20">
              <p className="text-xs text-red-200">{result.error}</p>
              {result.error_class && (
                <p className="text-xs text-red-300 mt-1">error_class: {result.error_class}</p>
              )}
            </div>
          )}
        </GlassCard>
      )}
    </div>
  );
}
