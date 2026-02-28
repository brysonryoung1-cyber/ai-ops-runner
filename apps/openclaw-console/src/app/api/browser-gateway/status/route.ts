/**
 * GET /api/browser-gateway/status?session_id=...
 *
 * Proxy to Browser Gateway server: get session status.
 * No secrets logged.
 */

import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";

const GATEWAY_URL = process.env.BROWSER_GATEWAY_URL || "http://127.0.0.1:8890";

export async function GET(req: NextRequest) {
  const sessionId = req.nextUrl.searchParams.get("session_id");
  if (!sessionId) {
    return NextResponse.json(
      { ok: false, error: "session_id required" },
      { status: 400 },
    );
  }

  try {
    const resp = await fetch(
      `${GATEWAY_URL}/session/status?session_id=${encodeURIComponent(sessionId)}`,
      { signal: AbortSignal.timeout(5000) },
    );
    const data = await resp.json();
    return NextResponse.json(data, { status: resp.status });
  } catch (e) {
    return NextResponse.json(
      {
        ok: false,
        error: "Browser Gateway server unreachable",
        error_class: "BROWSER_GATEWAY_UNREACHABLE",
      },
      { status: 502 },
    );
  }
}
