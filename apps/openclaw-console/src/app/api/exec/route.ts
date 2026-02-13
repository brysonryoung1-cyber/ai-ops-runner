import { NextRequest, NextResponse } from "next/server";
import { executeAction, checkConnectivity } from "@/lib/ssh";
import { resolveAction } from "@/lib/allowlist";

/**
 * POST /api/exec
 * Body: { "action": "doctor" | "apply" | "guard" | "ports" | "timer" | "journal" | "artifacts" }
 *
 * Executes an allowlisted SSH command against the configured AIOPS host.
 * Returns structured JSON with stdout, stderr, exit code, and timing.
 */
export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const actionName = body?.action;

    if (!actionName || typeof actionName !== "string") {
      return NextResponse.json(
        { ok: false, error: 'Missing or invalid "action" field.' },
        { status: 400 }
      );
    }

    // Validate action is allowlisted before doing anything
    const action = resolveAction(actionName);
    if (!action) {
      return NextResponse.json(
        {
          ok: false,
          error: `Action "${actionName}" is not allowlisted. Available: doctor, apply, guard, ports, timer, journal, artifacts.`,
        },
        { status: 403 }
      );
    }

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
