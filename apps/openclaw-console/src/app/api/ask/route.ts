/**
 * POST /api/ask — Ask OpenClaw (read-only grounded Q&A).
 *
 * Input: { question, project_id?, run_id?, engine? }
 * Generates or reuses State Pack via system.state_pack, loads artifacts,
 * returns answer with citations. Hard rule: citations[] empty -> 422.
 */

import { NextRequest, NextResponse } from "next/server";
import { existsSync, readFileSync, readdirSync, statSync } from "fs";
import { join } from "path";
import { executeAction } from "@/lib/hostd";
import { validateAskResponse, normalizeTaskRequest } from "@/lib/ocl";

export const dynamic = "force-dynamic";

const STATE_PACK_MAX_AGE_MIN = 5;
const RUNNER_TIMEOUT_MS = 30000;

function getArtifactsRoot(): string {
  if (process.env.OPENCLAW_ARTIFACTS_ROOT) return process.env.OPENCLAW_ARTIFACTS_ROOT;
  const repo = process.env.OPENCLAW_REPO_ROOT || process.cwd();
  return join(repo, "artifacts");
}

function getLatestStatePackRunId(): string | null {
  const base = join(getArtifactsRoot(), "system", "state_pack");
  if (!existsSync(base)) return null;
  const dirs = readdirSync(base, { withFileTypes: true })
    .filter((e) => e.isDirectory())
    .map((e) => e.name)
    .sort()
    .reverse();
  return dirs[0] ?? null;
}

function isStatePackFresh(runId: string): boolean {
  const dir = join(getArtifactsRoot(), "system", "state_pack", runId);
  if (!existsSync(dir)) return false;
  try {
    const summaryPath = join(dir, "SUMMARY.md");
    if (existsSync(summaryPath)) {
      const stat = statSync(summaryPath);
      const ageMin = (Date.now() - stat.mtimeMs) / 60000;
      return ageMin < STATE_PACK_MAX_AGE_MIN;
    }
  } catch {
    // ignore
  }
  return false;
}

function getLatestInvariantsPath(): string | null {
  const artifactsRoot = getArtifactsRoot();
  const reconcileBase = join(artifactsRoot, "system", "reconcile");
  if (!existsSync(reconcileBase)) return null;
  const dirs = readdirSync(reconcileBase, { withFileTypes: true })
    .filter((e) => e.isDirectory())
    .map((e) => e.name)
    .sort()
    .reverse();
  for (const d of dirs) {
    const invPath = join(reconcileBase, d, "invariants_after.json");
    if (existsSync(invPath)) return `artifacts/system/reconcile/${d}/invariants_after.json`;
    const invBefore = join(reconcileBase, d, "invariants_before.json");
    if (existsSync(invBefore)) return `artifacts/system/reconcile/${d}/invariants_before.json`;
  }
  const incidentsBase = join(artifactsRoot, "incidents");
  if (!existsSync(incidentsBase)) return null;
  const incDirs = readdirSync(incidentsBase, { withFileTypes: true })
    .filter((e) => e.isDirectory())
    .map((e) => e.name)
    .sort()
    .reverse();
  for (const id of incDirs.slice(0, 5)) {
    const invPath = join(incidentsBase, id, "invariants_after.json");
    if (existsSync(invPath)) return `artifacts/incidents/${id}/invariants_after.json`;
    const invBefore = join(incidentsBase, id, "invariants_before.json");
    if (existsSync(invBefore)) return `artifacts/incidents/${id}/invariants_before.json`;
  }
  return null;
}

function getLatestIncidentId(): string | null {
  const incidentsBase = join(getArtifactsRoot(), "incidents");
  if (!existsSync(incidentsBase)) return null;
  const dirs = readdirSync(incidentsBase, { withFileTypes: true })
    .filter((e) => e.isDirectory())
    .map((e) => e.name)
    .sort()
    .reverse();
  return dirs[0] ?? null;
}

function buildFallbackAnswer(statePackDir: string, question: string): { answer: string; citations: string[]; recommended_next_action?: { action: string; read_only: boolean } } {
  const base = join(getArtifactsRoot(), "system", "state_pack", statePackDir);
  const citations: string[] = [];
  for (const name of ["health_public.json", "autopilot_status.json", "SUMMARY.md"]) {
    const path = join(base, name);
    if (existsSync(path)) {
      citations.push(`artifacts/system/state_pack/${statePackDir}/${name}`);
    }
  }
  const invPath = getLatestInvariantsPath();
  if (invPath) citations.push(invPath);
  const latestIncident = getLatestIncidentId();
  if (latestIncident) citations.push(`artifacts/incidents/${latestIncident}/SUMMARY.md`);

  if (citations.length === 0) {
    return {
      answer: "State pack not loaded. Run system.state_pack action first.",
      citations: [],
      recommended_next_action: { action: "system.state_pack", read_only: true },
    };
  }

  const q = question.toLowerCase();
  if (q.includes("drifted") || q.includes("drift")) {
    let driftAnswer = "Drift status: check invariants. ";
    if (invPath) {
      try {
        const invFull = join(getArtifactsRoot(), invPath.replace(/^artifacts\//, ""));
        const inv = JSON.parse(readFileSync(invFull, "utf-8"));
        const allPass = inv.all_pass === true;
        driftAnswer = allPass
          ? "No drift detected. All invariants pass. "
          : `Drift detected. Invariants: ${(inv.invariants || []).filter((i: { pass?: boolean }) => !i.pass).map((i: { id?: string }) => i.id).join(", ")} failed. `;
      } catch {
        driftAnswer += "Could not parse invariants. ";
      }
    }
    driftAnswer += `Cited: ${invPath || "invariants_after.json"}. Run system.reconcile to heal.`;
    return {
      answer: driftAnswer,
      citations,
      recommended_next_action: { action: "system.reconcile", read_only: false },
    };
  }
  if (q.includes("broken") || q.includes("fail")) {
    return {
      answer: `State pack loaded. Check artifacts: ${citations.slice(0, 3).join(", ")}. For failures, run doctor or openclaw_hq_audit. Recent incidents: ${latestIncident ? `artifacts/incidents/${latestIncident}` : "none"}.`,
      citations,
      recommended_next_action: { action: "system.reconcile", read_only: false },
    };
  }
  if (q.includes("novnc") || q.includes("reachable")) {
    return {
      answer: `State pack loaded. Check tailscale_serve.txt and ports.txt in ${statePackDir}. noVNC typically on port 6080. Canonical URL: https://<host>/novnc/vnc.html?autoconnect=1&path=/websockify`,
      citations,
      recommended_next_action: { action: "playbook.recover_novnc_ws", read_only: false },
    };
  }
  if (q.includes("soma") && q.includes("waiting")) {
    return {
      answer: `State pack loaded. Check autopilot_status.json and latest_runs_index.json for Soma status.`,
      citations,
      recommended_next_action: { action: "system.state_pack", read_only: true },
    };
  }
  return {
    answer: `State pack loaded (${statePackDir}). Files: ${citations.join(", ")}. LLM not available — run system.state_pack and check RUNNER_API_URL.`,
    citations,
    recommended_next_action: { action: "system.state_pack", read_only: true },
  };
}

export async function POST(req: NextRequest) {
  const origin = req.headers.get("origin");
  const secFetchSite = req.headers.get("sec-fetch-site");
  const host = req.headers.get("host") ?? "";
  const allowed =
    (origin && (origin.includes("127.0.0.1") || origin.includes("localhost"))) ||
    secFetchSite === "same-origin" ||
    host.startsWith("127.0.0.1") ||
    host.startsWith("localhost") ||
    (process.env.OPENCLAW_TRUST_TAILSCALE === "1" && host.includes(".ts.net"));
  if (!allowed) {
    return NextResponse.json({ ok: false, error: "Forbidden" }, { status: 403 });
  }

  let body: { question?: string; project_id?: string; run_id?: string; engine?: string };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json(
      { ok: false, error: "Invalid JSON body", error_class: "INVALID_JSON" },
      { status: 400 }
    );
  }

  const question = typeof body.question === "string" ? body.question.trim() : "";
  if (!question) {
    return NextResponse.json(
      {
        ok: false,
        error: "question is required",
        error_class: "MISSING_QUESTION",
        recommended_next_action: { action: "system.state_pack", read_only: true },
      },
      { status: 400 }
    );
  }

  const projectId = typeof body.project_id === "string" ? body.project_id : undefined;
  const runId = typeof body.run_id === "string" ? body.run_id : undefined;
  const engineOverride = typeof body.engine === "string" ? body.engine : undefined;
  const enginePerRequest = process.env.ASK_ENGINE_PER_REQUEST === "1";
  const engine = enginePerRequest && engineOverride ? engineOverride : process.env.ASK_ENGINE || "default";

  let statePackDir: string;

  const cachedRunId = getLatestStatePackRunId();
  if (cachedRunId && isStatePackFresh(cachedRunId)) {
    statePackDir = `artifacts/system/state_pack/${cachedRunId}`;
  } else {
    const result = await executeAction("system.state_pack");
    if (!result.ok) {
      return NextResponse.json(
        {
          ok: false,
          error: "State pack generation failed",
          error_class: "STATE_PACK_FAILED",
          stderr: result.stderr?.slice(0, 500),
          recommended_next_action: { action: "doctor", read_only: true },
        },
        { status: 503 }
      );
    }
    let ocl: { run_id?: string; artifact_dir?: string };
    try {
      ocl = JSON.parse(result.stdout || "{}");
    } catch {
      ocl = {};
    }
    const spRunId = ocl.run_id ?? ocl.artifact_dir?.split("/").pop();
    if (!spRunId) {
      return NextResponse.json(
        {
          ok: false,
          error: "State pack result missing run_id",
          error_class: "STATE_PACK_NO_RUN_ID",
          recommended_next_action: { action: "system.state_pack", read_only: true },
        },
        { status: 503 }
      );
    }
    statePackDir = ocl.artifact_dir ?? `artifacts/system/state_pack/${spRunId}`;
  }

  const runnerUrl = process.env.RUNNER_API_URL;
  if (runnerUrl) {
    try {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), RUNNER_TIMEOUT_MS);
      const res = await fetch(`${runnerUrl.replace(/\/$/, "")}/ask`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question,
          state_pack_dir: statePackDir.startsWith("artifacts/") ? statePackDir : `artifacts/${statePackDir}`,
          project_id: projectId,
          run_id: runId,
          engine: engine === "microgpt" ? "microgpt" : "default",
        }),
        signal: controller.signal,
      });
      clearTimeout(timeout);
      if (res.ok) {
        const data = await res.json();
        if (validateAskResponse(data)) {
          return NextResponse.json({
            ok: true,
            answer: data.answer,
            citations: data.citations,
            recommended_next_action: normalizeTaskRequest(data.recommended_next_action) ?? {
              action: "system.state_pack",
              read_only: true,
            },
            confidence: data.confidence,
            state_pack_run_id: statePackDir.split("/").pop(),
          });
        }
      }
      if (res.status === 422) {
        const err = await res.json().catch(() => ({}));
        return NextResponse.json(
          {
            ok: false,
            error: err.detail?.message ?? "No citations available",
            error_class: "NO_CITATIONS",
            recommended_next_action: err.detail?.recommended_next_action ?? { action: "system.state_pack", read_only: true },
          },
          { status: 422 }
        );
      }
    } catch {
      // Fall through to fallback
    }
  }

  const runIdPart = statePackDir.split("/").pop() ?? "";
  const fallback = buildFallbackAnswer(runIdPart, question);
  if (fallback.citations.length === 0) {
    return NextResponse.json(
      {
        ok: false,
        error: "No citations available. State pack could not be loaded. Refusing answer without citations.",
        error_class: "NO_CITATIONS",
        recommended_next_action: { action: "system.state_pack", read_only: true },
      },
      { status: 422 }
    );
  }

  return NextResponse.json({
    ok: true,
    answer: fallback.answer,
    citations: fallback.citations,
    recommended_next_action: fallback.recommended_next_action ?? { action: "system.state_pack", read_only: true },
    confidence: "LOW",
    state_pack_run_id: runIdPart,
  });
}
