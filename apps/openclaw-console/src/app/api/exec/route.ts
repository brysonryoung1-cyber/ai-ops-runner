import { NextRequest, NextResponse } from "next/server";
import { readFileSync, existsSync, writeFileSync, mkdirSync } from "fs";
import { join } from "path";
import { executeAction, checkConnectivity, getHostdUrl, LONG_RUNNING_ACTIONS } from "@/lib/hostd";
import { acquireLock, releaseLock, getLockInfo, forceClearLock } from "@/lib/action-lock";
import {
  writeAuditEntry,
  deriveActor,
  hashParams,
} from "@/lib/audit";
import { buildRunRecord, buildRunRecordStart, writeRunRecord } from "@/lib/run-recorder";

export const runtime = "nodejs";

/**
 * Compute allowed origins dynamically based on configured port.
 * Supports OPENCLAW_CONSOLE_PORT env var (default 8787).
 * Also allows Tailscale HTTPS origins for VPS access via phone.
 */
function getAllowedOrigins(): Set<string> {
  const port = process.env.OPENCLAW_CONSOLE_PORT || process.env.PORT || "8787";
  const origins = new Set([
    `http://127.0.0.1:${port}`,
    `http://localhost:${port}`,
  ]);
  // Allow Tailscale serve HTTPS origin if configured
  const tsHostname = process.env.OPENCLAW_TAILSCALE_HOSTNAME;
  if (tsHostname) {
    origins.add(`https://${tsHostname}`);
  }
  return origins;
}

/**
 * Validate request origin to prevent cross-site request forgery.
 * Fail-closed: rejects requests that cannot be verified as same-origin.
 *
 * Checks (in order):
 *  1. Origin header matches an allowed localhost origin, OR
 *  2. Sec-Fetch-Site header is "same-origin" (sent by all modern browsers
 *     for same-origin fetch(), including GET where Origin may be omitted).
 *
 * Rejects if neither condition is met.
 */
function validateOrigin(req: NextRequest): NextResponse | null {
  const allowedOrigins = getAllowedOrigins();

  const origin = req.headers.get("origin");
  if (origin && allowedOrigins.has(origin)) {
    return null; // Explicit same-origin — allow
  }

  const secFetchSite = req.headers.get("sec-fetch-site");
  if (secFetchSite === "same-origin") {
    return null; // Browser-verified same-origin — allow
  }

  // Same-host automation (curl from aiops-1 to itself): no Origin, Host is loopback
  const host = req.headers.get("host") ?? "";
  if (!origin && (host.startsWith("127.0.0.1") || host.startsWith("localhost"))) {
    return null;
  }

  // Tailscale-trusted automation: when OPENCLAW_TRUST_TAILSCALE=1, allow requests from Tailscale hostname (e.g. curl from Mac/phone on tailnet)
  if (process.env.OPENCLAW_TRUST_TAILSCALE === "1") {
    const tsHostname = process.env.OPENCLAW_TAILSCALE_HOSTNAME;
    if (tsHostname && host.startsWith(tsHostname.split(":")[0])) {
      return null;
    }
    // Fallback: allow any *.ts.net host when trust_tailscale (Tailscale Serve HTTPS)
    if (host.includes(".ts.net")) {
      return null;
    }
  }

  const forbiddenPayload = {
    ok: false,
    error: "Forbidden",
    error_class: "ORIGIN_BLOCKED",
    reason: "Request origin could not be verified. This API only accepts same-origin requests from the local console.",
    required_header: "Origin or Sec-Fetch-Site: same-origin",
    trust_tailscale: process.env.OPENCLAW_TRUST_TAILSCALE === "1",
    hq_token_required: !!process.env.OPENCLAW_CONSOLE_TOKEN && process.env.OPENCLAW_TRUST_TAILSCALE !== "1",
    admin_token_loaded: typeof process.env.OPENCLAW_ADMIN_TOKEN === "string" && process.env.OPENCLAW_ADMIN_TOKEN.length > 0,
    origin_seen: req.headers.get("origin") ?? null,
    origin_allowed: false,
  };
  recordForbiddenEvent({ ...forbiddenPayload, timestamp: new Date().toISOString() });
  return NextResponse.json(forbiddenPayload, { status: 403 });
}

function recordForbiddenEvent(event: Record<string, unknown>) {
  try {
    const artifactsRoot = process.env.OPENCLAW_ARTIFACTS_ROOT ||
      join(process.env.OPENCLAW_REPO_ROOT || process.cwd(), "artifacts");
    mkdirSync(artifactsRoot, { recursive: true });
    writeFileSync(
      join(artifactsRoot, ".last_forbidden.json"),
      JSON.stringify(event, null, 2)
    );
  } catch {
    // Best-effort: never block request handling for diagnostics
  }
}

/** Read maintenance mode state (deploy pipeline sets/clears). DoD requests with matching deploy_run_id are allowed. */
function getMaintenanceMode(): { maintenance_mode: boolean; deploy_run_id?: string } {
  try {
    const repoRoot = process.env.OPENCLAW_REPO_ROOT || process.cwd();
    const path = join(repoRoot, "artifacts", ".maintenance_mode");
    if (!existsSync(path)) return { maintenance_mode: false };
    const raw = readFileSync(path, "utf-8").trim();
    if (!raw) return { maintenance_mode: false };
    const data = JSON.parse(raw) as { maintenance_mode?: boolean; deploy_run_id?: string };
    return {
      maintenance_mode: data.maintenance_mode === true,
      deploy_run_id: typeof data.deploy_run_id === "string" ? data.deploy_run_id : undefined,
    };
  } catch {
    return { maintenance_mode: false };
  }
}

/** Background execution for long-running actions. Writes run record and releases lock on completion. */
async function runActionAsync(
  actionName: string,
  runId: string,
  startedAt: Date,
  actor: string
): Promise<void> {
  try {
    const result = await executeAction(actionName);
    const finishedAt = new Date();

    if (result.httpStatus === 423) {
      writeAuditEntry({
        timestamp: finishedAt.toISOString(),
        actor,
        action_name: actionName,
        params_hash: hashParams({ action: actionName }),
        exit_code: null,
        duration_ms: finishedAt.getTime() - startedAt.getTime(),
        error: `error_class: ${result.error_class ?? "LANE_LOCKED_SOMA_FIRST"}`,
      });
      writeRunRecord(
        buildRunRecord(
          actionName,
          startedAt,
          finishedAt,
          null,
          false,
          result.error_class ?? "LANE_LOCKED_SOMA_FIRST",
          runId
        )
      );
    } else {
      let errorForRecord = result.error || null;
      const isProjectAction =
        actionName === "soma_kajabi_phase0" ||
        actionName === "soma_kajabi_auto_finish" ||
        actionName === "soma_run_to_done" ||
        actionName === "soma_kajabi_reauth_and_resume" ||
        actionName === "soma_kajabi_session_check" ||
        actionName === "soma_zane_finish_plan" ||
        actionName === "openclaw_hq_audit" ||
        actionName.startsWith("pred_markets.");
      if (isProjectAction && result.stdout) {
        try {
          const parsed = JSON.parse(result.stdout.trim().split("\n").pop() || "{}");
          if (parsed.error_class) {
            errorForRecord = `error_class: ${parsed.error_class}`;
          }
        } catch {
          /* ignore */
        }
      }

      writeAuditEntry({
        timestamp: finishedAt.toISOString(),
        actor,
        action_name: actionName,
        params_hash: hashParams({ action: actionName }),
        exit_code: result.exitCode,
        duration_ms: result.durationMs,
        ...(errorForRecord != null && { error: errorForRecord }),
      });

      const runRecord = buildRunRecord(
        actionName,
        startedAt,
        finishedAt,
        result.exitCode,
        result.ok,
        errorForRecord,
        runId,
        undefined,
        result.artifact_dir ?? undefined
      );
      writeRunRecord(runRecord);
    }
  } catch (err) {
    const finishedAt = new Date();
    const errorMsg = err instanceof Error ? err.message : String(err);
    writeAuditEntry({
      timestamp: finishedAt.toISOString(),
      actor,
      action_name: actionName,
      params_hash: hashParams({ action: actionName }),
      exit_code: null,
      duration_ms: finishedAt.getTime() - startedAt.getTime(),
      ...(errorMsg && { error: errorMsg }),
    });
    writeRunRecord(
      buildRunRecord(actionName, startedAt, finishedAt, null, false, errorMsg, runId)
    );
  } finally {
    releaseLock(actionName);
  }
}

/**
 * POST /api/exec
 * Body: { "action": "doctor" | "apply" | "guard" | "ports" | "timer" | "journal" | "artifacts" }
 *
 * Executes an allowlisted action via Host Executor (hostd on localhost). No SSH.
 * Returns structured JSON with stdout, stderr, exit code, and timing.
 *
 * Protected by:
 *  1. Token auth (middleware — X-OpenClaw-Token header)
 *  2. Origin validation (CSRF — this handler)
 *  3. Command allowlist (allowlist.ts) + hostd allowlist
 *  4. Action lock (prevents overlapping execution)
 *  5. Audit log (durable entry for every action)
 */
export async function POST(req: NextRequest) {
  // CSRF: reject cross-origin or missing-origin requests
  const originError = validateOrigin(req);
  if (originError) return originError;

  let body: Record<string, unknown>;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json(
      { ok: false, error: "Invalid or missing JSON body." },
      { status: 400 }
    );
  }

  // Schema validation: body must be a plain object with exactly { action: string }
  if (
    body === null ||
    typeof body !== "object" ||
    Array.isArray(body)
  ) {
    return NextResponse.json(
      { ok: false, error: "Request body must be a JSON object." },
      { status: 400 }
    );
  }

  const actionName = body?.action;
  if (!actionName || typeof actionName !== "string") {
    return NextResponse.json(
      { ok: false, error: 'Missing or invalid "action" field. Must be a string.' },
      { status: 400 }
    );
  }

  // Reject unexpected fields (defense in depth)
  const allowedFields = new Set(["action"]);
  const extraFields = Object.keys(body).filter((k) => !allowedFields.has(k));
  if (extraFields.length > 0) {
    return NextResponse.json(
      {
        ok: false,
        error: `Unexpected fields: ${extraFields.join(", ")}. Only "action" is accepted.`,
      },
      { status: 400 }
    );
  }

  // Admin-only actions: deploy_and_verify (and ship_only when exposed). Fail-closed.
  const adminOnlyActions = new Set(["deploy_and_verify"]);
  if (adminOnlyActions.has(actionName)) {
    const adminToken = process.env.OPENCLAW_ADMIN_TOKEN;
    if (typeof adminToken !== "string" || adminToken.length === 0) {
      return NextResponse.json(
        { ok: false, error: "admin not configured" },
        { status: 503 }
      );
    }
    const provided = req.headers.get("x-openclaw-token");
    if (provided !== adminToken) {
      return NextResponse.json(
        {
          ok: false,
          error: "Forbidden",
          error_class: "ADMIN_TOKEN_MISSING",
          reason: "Deploy+Verify requires admin token. Provide the OPENCLAW_ADMIN_TOKEN via X-OpenClaw-Token header.",
          required_header: "X-OpenClaw-Token (admin)",
          trust_tailscale: process.env.OPENCLAW_TRUST_TAILSCALE === "1",
          hq_token_required: true,
          admin_token_loaded: typeof adminToken === "string" && adminToken.length > 0,
          origin_seen: req.headers.get("origin") ?? null,
          origin_allowed: true,
        },
        { status: 403 }
      );
    }
  }

  // Unlock action: safe clear of soma_kajabi_auto_finish lock when no active run
  const AUTO_FINISH_ACTION = "soma_kajabi_auto_finish";
  if (actionName === "soma_auto_finish_unlock") {
    const lockInfo = getLockInfo(AUTO_FINISH_ACTION);
    if (!lockInfo) {
      return NextResponse.json(
        { ok: true, unlocked: true, message: "No lock held. Auto-Finish is not running." },
        { status: 200 }
      );
    }
    // Lock exists: refuse if not stale (active run in progress)
    const startedAt = new Date(lockInfo.started_at).getTime();
    const STALE_MS = 30 * 60 * 1000;
    if (Date.now() - startedAt < STALE_MS) {
      return NextResponse.json(
        {
          ok: false,
          error_class: "ACTIVE_RUN_EXISTS",
          message: "Cannot unlock: Auto-Finish run is active. Join via /api/runs or wait for completion.",
          active_run_id: lockInfo.active_run_id,
          started_at: lockInfo.started_at,
        },
        { status: 409 }
      );
    }
    forceClearLock(AUTO_FINISH_ACTION);
    return NextResponse.json(
      { ok: true, unlocked: true, message: "Stale lock cleared. Auto-Finish can be started." },
      { status: 200 }
    );
  }

  // Maintenance mode: block non-DoD doctor triggers during deploy
  if (actionName === "doctor") {
    const maintenance = getMaintenanceMode();
    if (maintenance.maintenance_mode && maintenance.deploy_run_id) {
      const dodRun = req.headers.get("x-openclaw-dod-run");
      if (dodRun !== maintenance.deploy_run_id) {
        return NextResponse.json(
          { ok: false, error_class: "MAINTENANCE_MODE", action: "doctor" },
          { status: 503 }
        );
      }
    }
  }

  // Action lock — single-flight + join: 409 ALWAYS includes active_run_id for poll /api/runs?id=<run_id>
  const lockResult = acquireLock(actionName);
  if (!lockResult.acquired) {
    const existing = lockResult.existing;
    const lockInfo = getLockInfo(actionName);
    const runId = existing?.runId ?? lockInfo?.active_run_id;
    const startedAt = existing?.startedAt ?? (lockInfo?.started_at ? new Date(lockInfo.started_at).getTime() : undefined);
    const payload: Record<string, unknown> = {
      ok: false,
      error_class: "ALREADY_RUNNING",
      action: actionName,
      active_run_id: runId ?? "(unknown)",
      active_run_status: "running",
    };
    if (runId && runId !== "(unknown)") {
      if (startedAt != null) payload.started_at = new Date(startedAt).toISOString();
      if (lockInfo?.artifact_dir) payload.artifact_dir = lockInfo.artifact_dir;
    } else {
      payload.error = `Action "${actionName}" is already running. Join via /api/runs?id=<active_run_id>.`;
    }
    return NextResponse.json(payload, { status: 409 });
  }

  const runId = lockResult.runId!;
  const actor = deriveActor(req.headers.get("x-openclaw-token"));
  const params = { action: actionName };
  const startedAt = new Date();

  // Probe hostd with retry/backoff (10s, 20s, 40s; total ≤90s) before failing
  const backoffMs = [10_000, 20_000, 40_000];
  let hostdProbe = await checkConnectivity();
  for (let i = 0; i < backoffMs.length && !hostdProbe.ok; i++) {
    await new Promise((r) => setTimeout(r, backoffMs[i]));
    hostdProbe = await checkConnectivity();
    if (hostdProbe.ok) {
      console.warn(`[exec] RECOVERED_AFTER_RETRY action=${actionName} run_id=${runId}`);
      break;
    }
  }
  if (!hostdProbe.ok) {
    const url = getHostdUrl() ?? "(OPENCLAW_HOSTD_URL not set)";
    const errorSummary = `Host Executor unreachable at ${url}: ${hostdProbe.error ?? "unknown"}. Try restarting openclaw-hostd (systemctl restart openclaw-hostd) and check console→hostd connectivity (OPENCLAW_HOSTD_URL e.g. http://127.0.0.1:8877 or http://host.docker.internal:8877).`;
    const finishedAt = new Date();
    const artifactsRoot = process.env.OPENCLAW_ARTIFACTS_ROOT || join(process.env.OPENCLAW_REPO_ROOT || process.cwd(), "artifacts");
    const hostdDir = join(artifactsRoot, "hostd", `unreachable_${runId}`);
    try {
      mkdirSync(hostdDir, { recursive: true });
      writeFileSync(join(hostdDir, "stderr.txt"), errorSummary + "\n", "utf-8");
    } catch {
      // best-effort; run record still written
    }
    const artifactDir = `artifacts/hostd/unreachable_${runId}`;
    writeAuditEntry({
      timestamp: finishedAt.toISOString(),
      actor,
      action_name: actionName,
      params_hash: hashParams(params),
      exit_code: null,
      duration_ms: finishedAt.getTime() - startedAt.getTime(),
      error: errorSummary,
    });
    writeRunRecord(
      buildRunRecord(actionName, startedAt, finishedAt, null, false, errorSummary, runId, undefined, artifactDir, "HOSTD_UNREACHABLE")
    );
    releaseLock(actionName);
    return NextResponse.json(
      {
        ok: false,
        error: errorSummary,
        error_class: "HOSTD_UNREACHABLE",
        error_summary: errorSummary,
        action: actionName,
        run_id: runId,
        artifact_dir: artifactDir,
      },
      { status: 502 }
    );
  }

  // Async path: long-running actions return 202 immediately; client polls /api/runs?id=<run_id>
  if (LONG_RUNNING_ACTIONS.has(actionName)) {
    writeRunRecord(buildRunRecordStart(actionName, startedAt, runId, undefined, "running"));
    void runActionAsync(actionName, runId, startedAt, actor);
    return NextResponse.json(
      {
        ok: true,
        run_id: runId,
        status: "running",
        message: "Poll GET /api/runs?id=" + runId + " for status",
      },
      { status: 202 }
    );
  }

  try {
    // executeAction validates allowlist and calls hostd (fail-closed)
    const result = await executeAction(actionName);

    // Soma-first gate: pass through 423 Locked from hostd
    if (result.httpStatus === 423) {
      const finishedAt = new Date();
      writeAuditEntry({
        timestamp: finishedAt.toISOString(),
        actor: deriveActor(req.headers.get("x-openclaw-token")),
        action_name: actionName,
        params_hash: hashParams({ action: actionName }),
        exit_code: null,
        duration_ms: finishedAt.getTime() - startedAt.getTime(),
        error: `error_class: ${result.error_class ?? "LANE_LOCKED_SOMA_FIRST"}`,
      });
      writeRunRecord(
        buildRunRecord(
          actionName,
          startedAt,
          finishedAt,
          null,
          false,
          result.error_class ?? "LANE_LOCKED_SOMA_FIRST",
          runId
        )
      );
      return NextResponse.json(
        {
          ok: false,
          error_class: result.error_class ?? "LANE_LOCKED_SOMA_FIRST",
          required_condition: result.required_condition ?? "Set gates.allow_orb_backtests=true after Soma Phase0 baseline PASS",
          action: actionName,
          run_id: runId,
          artifact_dir: result.artifact_dir,
        },
        { status: 423 }
      );
    }

    const finishedAt = new Date();

    // Parse project action stdout for error_class (soma_kajabi_phase0, pred_markets.*)
    let errorForRecord = result.error || null;
    let responsePayload: Record<string, unknown> = { ...result };
    const isProjectAction =
      actionName === "soma_kajabi_phase0" ||
      actionName === "soma_kajabi_auto_finish" ||
      actionName === "soma_kajabi_reauth_and_resume" ||
      actionName === "soma_kajabi_session_check" ||
      actionName === "soma_zane_finish_plan" ||
      actionName === "openclaw_hq_audit" ||
      actionName.startsWith("pred_markets.");
    if (isProjectAction && result.stdout) {
      try {
        const parsed = JSON.parse(result.stdout.trim().split("\n").pop() || "{}");
        if (parsed.error_class) {
          errorForRecord = `error_class: ${parsed.error_class}${parsed.recommended_next_action ? ` | recommended_next_action: ${parsed.recommended_next_action}` : ""}`;
          responsePayload = {
            ...result,
            error_class: parsed.error_class,
            recommended_next_action: parsed.recommended_next_action,
            artifact_paths: parsed.artifact_paths,
            artifact_dir: parsed.artifact_dir ?? result.artifact_dir,
            ...((parsed.error_class === "NOVNC_BACKEND_UNAVAILABLE" || parsed.error_class === "NOVNC_NOT_READY") &&
              parsed.journal_artifact && {
                journal_artifact: parsed.journal_artifact,
              }),
          };
        }
      } catch {
        // Ignore parse errors
      }
    }
    // For non-project actions (e.g. apply), persist stderr so Runs UI shows real failure (e.g. SSH 255)
    if (errorForRecord == null && !result.ok && result.stderr && typeof result.stderr === "string") {
      const maxLen = 2000;
      errorForRecord = result.stderr.length <= maxLen ? result.stderr.trim() : result.stderr.trim().slice(-maxLen);
    }

    // Write audit entry
    writeAuditEntry({
      timestamp: finishedAt.toISOString(),
      actor,
      action_name: actionName,
      params_hash: hashParams(params),
      exit_code: result.exitCode,
      duration_ms: result.durationMs,
      ...(errorForRecord != null && { error: errorForRecord }),
    });

    // Write run record (even on action failure — fail-closed recorder); use lock runId for join semantics
    const runRecord = buildRunRecord(
      actionName,
      startedAt,
      finishedAt,
      result.exitCode,
      result.ok,
      errorForRecord,
      runId,
      undefined,
      result.artifact_dir ?? undefined
    );
    writeRunRecord(runRecord);

    // Always include run_id for hq_apply.sh and Runs UI polling
    responsePayload.run_id = runId;

    return NextResponse.json(responsePayload, { status: result.ok ? 200 : 502 });
  } catch (err) {
    const finishedAt = new Date();
    const duration_ms = finishedAt.getTime() - startedAt.getTime();
    const errorMsg = err instanceof Error ? err.message : String(err);

    // Audit the failure
    writeAuditEntry({
      timestamp: finishedAt.toISOString(),
      actor,
      action_name: actionName,
      params_hash: hashParams(params),
      exit_code: null,
      duration_ms,
      ...(errorMsg && { error: errorMsg }),
    });

    // Write run record even on internal errors (fail-closed); use same runId
    const runRecord = buildRunRecord(
      actionName,
      startedAt,
      finishedAt,
      null,
      false,
      errorMsg,
      runId,
      undefined,
      undefined
    );
    writeRunRecord(runRecord);

    return NextResponse.json(
      { ok: false, error: `Internal error: ${errorMsg}` },
      { status: 500 }
    );
  } finally {
    releaseLock(actionName);
  }
}

/**
 * GET /api/exec?check=connectivity
 * Quick Host Executor (hostd) connectivity probe.
 *
 * Protected by:
 *  1. Token auth (middleware)
 *  2. Origin validation (CSRF)
 */
export async function GET(req: NextRequest) {
  // CSRF: reject cross-origin or missing-origin requests
  const originError = validateOrigin(req);
  if (originError) return originError;

  const check = req.nextUrl.searchParams.get("check");
  const action = req.nextUrl.searchParams.get("action");

  if (check === "connectivity") {
    const result = await checkConnectivity();
    return NextResponse.json(result, { status: result.ok ? 200 : 502 });
  }

  if (check === "lock" && action) {
    const lockInfo = getLockInfo(action);
    return NextResponse.json({
      ok: true,
      locked: !!lockInfo,
      active_run_id: lockInfo?.active_run_id ?? null,
      started_at: lockInfo?.started_at ?? null,
    });
  }

  return NextResponse.json(
    { error: "Use POST with { action } or GET with ?check=connectivity or ?check=lock&action=<name>" },
    { status: 400 }
  );
}
