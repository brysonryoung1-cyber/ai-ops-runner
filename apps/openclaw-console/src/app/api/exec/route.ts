import { NextRequest, NextResponse } from "next/server";
import { executeAction, checkConnectivity } from "@/lib/ssh";
import { acquireLock, releaseLock } from "@/lib/action-lock";
import {
  writeAuditEntry,
  deriveActor,
  hashParams,
} from "@/lib/audit";

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

  return NextResponse.json(
    {
      ok: false,
      error: "Forbidden: request origin could not be verified. This API only accepts same-origin requests from the local console.",
    },
    { status: 403 }
  );
}

/**
 * POST /api/exec
 * Body: { "action": "doctor" | "apply" | "guard" | "ports" | "timer" | "journal" | "artifacts" }
 *
 * Executes an allowlisted SSH command against the configured AIOPS host.
 * Returns structured JSON with stdout, stderr, exit code, and timing.
 *
 * Protected by:
 *  1. Token auth (middleware — X-OpenClaw-Token header)
 *  2. Origin validation (CSRF — this handler)
 *  3. Command allowlist (ssh.ts / allowlist.ts)
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

  const actionName = body?.action;
  if (!actionName || typeof actionName !== "string") {
    return NextResponse.json(
      { ok: false, error: 'Missing or invalid "action" field.' },
      { status: 400 }
    );
  }

  // Action lock — prevent overlapping execution
  if (!acquireLock(actionName)) {
    return NextResponse.json(
      {
        ok: false,
        error: `Action "${actionName}" is already running. Wait for it to complete.`,
      },
      { status: 409 }
    );
  }

  const actor = deriveActor(req.headers.get("x-openclaw-token"));
  const params = { action: actionName };
  const startTime = Date.now();

  try {
    // executeAction validates the allowlist internally (fail-closed)
    const result = await executeAction(actionName);

    // Write audit entry
    writeAuditEntry({
      timestamp: new Date().toISOString(),
      actor,
      action_name: actionName,
      params_hash: hashParams(params),
      exit_code: result.exitCode,
      duration_ms: result.durationMs,
      error: result.error,
    });

    return NextResponse.json(result, { status: result.ok ? 200 : 502 });
  } catch (err) {
    const duration_ms = Date.now() - startTime;
    const errorMsg = err instanceof Error ? err.message : String(err);

    // Audit the failure
    writeAuditEntry({
      timestamp: new Date().toISOString(),
      actor,
      action_name: actionName,
      params_hash: hashParams(params),
      exit_code: null,
      duration_ms,
      error: errorMsg,
    });

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
 * Quick SSH connectivity probe.
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
