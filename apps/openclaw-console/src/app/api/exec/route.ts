import { NextRequest, NextResponse } from "next/server";
import { executeAction, checkConnectivity } from "@/lib/ssh";

/** Allowed origins for CSRF protection (localhost-only console). */
const ALLOWED_ORIGINS = new Set([
  "http://127.0.0.1:8787",
  "http://localhost:8787",
]);

/**
 * Validate the request Origin header to prevent cross-site request forgery.
 * Fail-closed: rejects requests with missing or non-local Origin.
 */
function validateOrigin(req: NextRequest): NextResponse | null {
  const origin = req.headers.get("origin");
  if (!origin || !ALLOWED_ORIGINS.has(origin)) {
    return NextResponse.json(
      {
        ok: false,
        error: "Forbidden: invalid or missing Origin header. This API only accepts requests from the local console.",
      },
      { status: 403 }
    );
  }
  return null;
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

  try {
    const body = await req.json();
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
 */
export async function GET(req: NextRequest) {
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
