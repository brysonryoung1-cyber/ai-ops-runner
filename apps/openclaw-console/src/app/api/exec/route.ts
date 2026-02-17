import { NextRequest, NextResponse } from "next/server";
import { readFileSync, existsSync } from "fs";
import { join } from "path";
import { executeAction, checkConnectivity } from "@/lib/hostd";
import { acquireLock, releaseLock, getLockInfo } from "@/lib/action-lock";
import {
  writeAuditEntry,
  deriveActor,
  hashParams,
} from "@/lib/audit";
import { buildRunRecord, writeRunRecord } from "@/lib/run-recorder";

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

  return NextResponse.json(
    {
      ok: false,
      error: "Forbidden: request origin could not be verified. This API only accepts same-origin requests from the local console.",
    },
    { status: 403 }
  );
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
        { ok: false, error: "Deploy+Verify requires admin token." },
        { status: 403 }
      );
    }
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

  // Action lock — single-flight + join: 409 includes active_run_id for poll /api/runs?id=<run_id>
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
    };
    if (runId) {
      payload.active_run_id = runId;
      if (startedAt != null) payload.started_at = new Date(startedAt).toISOString();
    } else {
      payload.error = `Action "${actionName}" is already running. Wait for it to complete.`;
    }
    return NextResponse.json(payload, { status: 409 });
  }

  const runId = lockResult.runId!;
  const actor = deriveActor(req.headers.get("x-openclaw-token"));
  const params = { action: actionName };
  const startedAt = new Date();

  try {
    // executeAction validates allowlist and calls hostd (fail-closed)
    const result = await executeAction(actionName);

    const finishedAt = new Date();

    // Parse soma_kajabi_phase0 stdout for error_class, recommended_next_action, artifact_paths
    let errorForRecord = result.error || null;
    let responsePayload: Record<string, unknown> = { ...result };
    if (actionName === "soma_kajabi_phase0" && result.stdout) {
      try {
        const parsed = JSON.parse(result.stdout.trim().split("\n").pop() || "{}");
        if (parsed.error_class) {
          errorForRecord = `error_class: ${parsed.error_class}${parsed.recommended_next_action ? ` | recommended_next_action: ${parsed.recommended_next_action}` : ""}`;
          responsePayload = { ...result, error_class: parsed.error_class, recommended_next_action: parsed.recommended_next_action, artifact_paths: parsed.artifact_paths };
        }
      } catch {
        // Ignore parse errors
      }
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
      runId
    );
    writeRunRecord(runRecord);

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
      runId
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

  if (check === "connectivity") {
    const result = await checkConnectivity();
    return NextResponse.json(result, { status: result.ok ? 200 : 502 });
  }

  return NextResponse.json(
    { error: "Use POST with { action } or GET with ?check=connectivity" },
    { status: 400 }
  );
}
