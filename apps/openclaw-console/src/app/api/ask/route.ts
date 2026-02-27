/**
 * POST /api/ask — Ask OpenClaw (read-only grounded Q&A).
 *
 * Input: { question, project_id?, run_id?, mode? }
 * mode: "deterministic" (default, no LLM) | "summarize" (optional LLM rephrase)
 * Generates or reuses State Pack, loads artifacts, returns answer with citations.
 * Hard rule: citations[] empty -> 422.
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

interface AskCheck {
  name: string;
  pass: boolean;
  detail: string;
  citation?: string;
}

interface DeterministicResult {
  answer: string;
  citations: string[];
  checks: AskCheck[];
  recommended_next_action: { action: string; read_only: boolean };
  confidence: "HIGH" | "MED" | "LOW";
}

function buildDeterministicAnswer(
  statePackDir: string,
  question: string,
  artifactsRoot: string
): DeterministicResult {
  const base = join(artifactsRoot, "system", "state_pack", statePackDir);
  const citations: string[] = [];
  const checks: AskCheck[] = [];

  for (const name of ["health_public.json", "autopilot_status.json", "SUMMARY.md"]) {
    const path = join(base, name);
    const relPath = `artifacts/system/state_pack/${statePackDir}/${name}`;
    if (existsSync(path)) {
      citations.push(relPath);
      checks.push({
        name: name.replace(".json", "").replace(".md", ""),
        pass: true,
        detail: "OK",
        citation: relPath,
      });
    }
  }

  const invPath = getLatestInvariantsPath();
  if (invPath) {
    citations.push(invPath);
    try {
      const invFull = join(artifactsRoot, invPath.replace(/^artifacts\//, ""));
      const inv = JSON.parse(readFileSync(invFull, "utf-8"));
      const allPass = inv.all_pass === true;
      checks.push({
        name: "invariants",
        pass: allPass,
        detail: allPass ? "All pass" : "Some failed",
        citation: invPath,
      });
    } catch {
      checks.push({ name: "invariants", pass: false, detail: "Parse error", citation: invPath });
    }
  }

  const latestIncident = getLatestIncidentId();
  if (latestIncident) citations.push(`artifacts/incidents/${latestIncident}/SUMMARY.md`);

  const q = question.toLowerCase();

  // Intent: version/build/drift
  if (q.includes("drift") || q.includes("version") || q.includes("build") || q.includes("up to date")) {
    let driftAnswer = "Version/drift: ";
    const versionPath = join(base, "version.json");
    if (existsSync(versionPath)) {
      try {
        const v = JSON.parse(readFileSync(versionPath, "utf-8"));
        const drift = v.drift;
        if (drift === false) {
          driftAnswer = "Up to date. Deployed matches origin/main. ";
          checks.push({ name: "drift", pass: true, detail: "No drift", citation: `artifacts/system/state_pack/${statePackDir}/version.json` });
        } else if (drift === true) {
          driftAnswer = "DRIFT detected. Deployed != origin/main. Run deploy or reconcile. ";
          checks.push({ name: "drift", pass: false, detail: "Drift", citation: `artifacts/system/state_pack/${statePackDir}/version.json` });
        } else {
          driftAnswer += "Unknown (deploy_info may be missing). ";
        }
      } catch {
        driftAnswer += "Could not parse version. ";
      }
    } else if (invPath) {
      try {
        const invFull = join(artifactsRoot, invPath.replace(/^artifacts\//, ""));
        const inv = JSON.parse(readFileSync(invFull, "utf-8"));
        const allPass = inv.all_pass === true;
        driftAnswer = allPass
          ? "No drift detected. All invariants pass. "
          : `Drift detected. Invariants: ${(inv.invariants || []).filter((i: { pass?: boolean }) => !i.pass).map((i: { id?: string }) => i.id).join(", ")} failed. `;
      } catch {
        driftAnswer += "Could not parse invariants. ";
      }
    }
    driftAnswer += `Cited: ${invPath || "invariants"}. Run system.reconcile to heal.`;
    return {
      answer: driftAnswer,
      citations,
      checks,
      recommended_next_action: { action: "system.reconcile", read_only: false },
      confidence: citations.length >= 3 ? "HIGH" : "MED",
    };
  }

  // Intent: novnc connectivity
  if (q.includes("novnc") || q.includes("reachable") || q.includes("websockify")) {
    const wsPath = join(base, "ws_probe.json");
    const novncPath = join(base, "novnc_http_check.json");
    let novncAnswer = "noVNC: ";
    if (existsSync(wsPath)) {
      try {
        const ws = JSON.parse(readFileSync(wsPath, "utf-8"));
        const allOk = ws.all_ok === true;
        const eps = ws.endpoints || {};
        const webOk = eps["/websockify"]?.ok;
        const novncOk = eps["/novnc/websockify"]?.ok;
        novncAnswer += allOk
          ? "Both WSS endpoints (/websockify, /novnc/websockify) hold >=10s. Ready for human. "
          : `WSS probes: /websockify=${webOk ? "OK" : "FAIL"}, /novnc/websockify=${novncOk ? "OK" : "FAIL"}. `;
        checks.push({ name: "ws_probe", pass: allOk, detail: allOk ? "Both endpoints OK" : "One or both failed", citation: `artifacts/system/state_pack/${statePackDir}/ws_probe.json` });
      } catch {
        novncAnswer += "Could not parse ws_probe. ";
      }
    }
    if (existsSync(novncPath)) {
      try {
        const nc = JSON.parse(readFileSync(novncPath, "utf-8"));
        const httpOk = nc.ok === true;
        novncAnswer += `HTTP /novnc/vnc.html: ${httpOk ? "200" : nc.status_code || "fail"}. `;
        checks.push({ name: "novnc_http", pass: httpOk, detail: httpOk ? "200" : "Not 200", citation: `artifacts/system/state_pack/${statePackDir}/novnc_http_check.json` });
      } catch {
        // ignore
      }
    }
    novncAnswer += `Canonical URL: https://<host>/novnc/vnc.html?autoconnect=1&path=/websockify`;
    return {
      answer: novncAnswer,
      citations,
      checks,
      recommended_next_action: { action: "playbook.recover_novnc_ws", read_only: false },
      confidence: citations.length >= 4 ? "HIGH" : "MED",
    };
  }

  // Intent: soma waiting/human gate
  if ((q.includes("soma") && q.includes("wait")) || q.includes("human gate") || q.includes("waiting")) {
    const apPath = join(base, "autopilot_status.json");
    const runsPath = join(base, "latest_runs_index.json");
    let somaAnswer = "Soma/autopilot: ";
    if (existsSync(apPath)) {
      try {
        const ap = JSON.parse(readFileSync(apPath, "utf-8"));
        somaAnswer += `Autopilot installed=${ap.installed}, enabled=${ap.enabled}. `;
        checks.push({ name: "autopilot", pass: ap.ok === true, detail: ap.ok ? "OK" : "Not ok", citation: `artifacts/system/state_pack/${statePackDir}/autopilot_status.json` });
      } catch {
        somaAnswer += "Could not parse autopilot. ";
      }
    }
    if (existsSync(runsPath)) {
      try {
        const runs = JSON.parse(readFileSync(runsPath, "utf-8"));
        somaAnswer += `Latest runs: ${JSON.stringify(runs.projects || {}).slice(0, 80)}... `;
      } catch {
        // ignore
      }
    }
    somaAnswer += "Check artifacts/soma_kajabi/human_gate/<run_id>/ for WAITING_FOR_HUMAN status. HumanGateWatcher auto-resumes on login.";
    return {
      answer: somaAnswer,
      citations,
      checks,
      recommended_next_action: { action: "system.state_pack", read_only: true },
      confidence: citations.length >= 3 ? "HIGH" : "MED",
    };
  }

  // Intent: autopilot status
  if (q.includes("autopilot") || q.includes("timer")) {
    const apPath = join(base, "autopilot_status.json");
    let apAnswer = "Autopilot: ";
    if (existsSync(apPath)) {
      try {
        const ap = JSON.parse(readFileSync(apPath, "utf-8"));
        apAnswer += `installed=${ap.installed}, enabled=${ap.enabled}, last_run_id=${ap.last_run_id || "—"}, fail_count=${ap.fail_count ?? "—"}. `;
        checks.push({ name: "autopilot", pass: ap.ok === true, detail: ap.ok ? "OK" : "Not ok", citation: `artifacts/system/state_pack/${statePackDir}/autopilot_status.json` });
      } catch {
        apAnswer += "Parse error. ";
      }
    } else {
      apAnswer += "autopilot_status.json missing. ";
    }
    apAnswer += "Reconcile timer runs every 5–10 min.";
    return {
      answer: apAnswer,
      citations,
      checks,
      recommended_next_action: { action: "system.state_pack", read_only: true },
      confidence: citations.length >= 2 ? "HIGH" : "LOW",
    };
  }

  // Intent: what changed since last deploy?
  if (q.includes("changed") || q.includes("since") || q.includes("deploy") || q.includes("last deploy")) {
    let changeAnswer = "Changes since last deploy: ";
    if (latestIncident) {
      changeAnswer += `Latest incident: artifacts/incidents/${latestIncident}. `;
      citations.push(`artifacts/incidents/${latestIncident}/SUMMARY.md`);
    }
    if (invPath) {
      changeAnswer += `Invariants: ${invPath}. `;
    }
    changeAnswer += "Check incidents ledger and reconcile deltas for deterministic summary. Run system.reconcile for current state.";
    return {
      answer: changeAnswer,
      citations,
      checks,
      recommended_next_action: { action: "system.reconcile", read_only: false },
      confidence: citations.length >= 2 ? "MED" : "LOW",
    };
  }

  // Intent: broken/fail (generic)
  if (q.includes("broken") || q.includes("fail")) {
    return {
      answer: `State pack loaded. Check artifacts: ${citations.slice(0, 3).join(", ")}. For failures, run doctor or openclaw_hq_audit. Recent incidents: ${latestIncident ? `artifacts/incidents/${latestIncident}` : "none"}.`,
      citations,
      checks,
      recommended_next_action: { action: "system.reconcile", read_only: false },
      confidence: citations.length >= 3 ? "MED" : "LOW",
    };
  }

  // Default fallback
  return {
    answer: `State pack loaded (${statePackDir}). Files: ${citations.join(", ")}. Ask is deterministic by default (no LLM). Use quick prompts for version, novnc, soma, autopilot.`,
    citations,
    checks,
    recommended_next_action: { action: "system.state_pack", read_only: true },
    confidence: citations.length >= 2 ? "MED" : "LOW",
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

  let body: { question?: string; project_id?: string; run_id?: string; mode?: string; engine?: string };
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

  const mode = typeof body.mode === "string" ? body.mode : "deterministic";
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
  if (runnerUrl && mode !== "summarize") {
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
          const checks = (data as { checks?: AskCheck[] }).checks || [];
          return NextResponse.json({
            ok: true,
            answer: data.answer,
            citations: data.citations,
            checks: checks.length > 0 ? checks : [{ name: "runner", pass: true, detail: "OK", citation: statePackDir }],
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
      // Fall through to deterministic fallback
    }
  }

  const runIdPart = statePackDir.split("/").pop() ?? "";
  const artifactsRoot = getArtifactsRoot();
  const fallback = buildDeterministicAnswer(runIdPart, question, artifactsRoot);

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
    checks: fallback.checks,
    recommended_next_action: fallback.recommended_next_action,
    confidence: fallback.confidence,
    state_pack_run_id: runIdPart,
  });
}
