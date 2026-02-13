import { NextRequest, NextResponse } from "next/server";
import { executeAction, checkConnectivity } from "@/lib/ssh";

/** Allowed origins for CSRF protection (localhost-only console). */
const ALLOWED_ORIGINS = new Set([
  "http://127.0.0.1:8787",
  "http://localhost:8787",
]);

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
  const origin = req.headers.get("origin");
  if (origin && ALLOWED_ORIGINS.has(origin)) {
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
 * Protected by Origin header validation (CSRF).
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

  try {
    const actionName = body?.action;

    if (!actionName || typeof actionName !== "string") {
      return NextResponse.json(
        { ok: false, error: 'Missing or invalid "action" field.' },
        { status: 400 }
      );
    }

    // executeAction validates the allowlist internally (fail-closed)
    const result = await executeAction(actionName);
    return NextResponse.json(result, { status: result.ok ? 200 : 502 });
  } catch (err) {
    return NextResponse.json(
      {
        ok: false,
        error: `Internal error: ${err instanceof Error ? err.message : String(err)}`,
      },
      { status: 500 }
    );
  }
}

/**
 * GET /api/exec?check=connectivity
 * Quick SSH connectivity probe.
 *
 * Protected by Origin header validation (CSRF).
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
