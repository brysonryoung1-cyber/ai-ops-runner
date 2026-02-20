import { NextRequest, NextResponse } from "next/server";
import { executeAction } from "@/lib/hostd";

export const dynamic = "force-dynamic";

/**
 * POST /api/autopilot/run_now
 *
 * Trigger an immediate autopilot tick by executing the allowlisted "autopilot_run_now" action via hostd.
 * Requires admin token (enforced at hostd level).
 */
export async function POST(req: NextRequest) {
  const adminToken = process.env.OPENCLAW_ADMIN_TOKEN;
  if (typeof adminToken !== "string" || adminToken.length === 0) {
    return NextResponse.json({ ok: false, error: "admin not configured" }, { status: 503 });
  }
  const provided = req.headers.get("x-openclaw-token");
  if (provided !== adminToken) {
    return NextResponse.json(
      { ok: false, error: "Forbidden", error_class: "ADMIN_TOKEN_MISSING" },
      { status: 403 }
    );
  }

  try {
    const result = await executeAction("autopilot_run_now");
    return NextResponse.json({
      ok: result.ok,
      stdout: result.stdout,
      stderr: result.stderr,
      exitCode: result.exitCode,
      artifact_dir: result.artifact_dir,
    }, { status: result.ok ? 200 : 502 });
  } catch (err) {
    return NextResponse.json(
      { ok: false, error: `Internal error: ${err instanceof Error ? err.message : String(err)}` },
      { status: 500 }
    );
  }
}
