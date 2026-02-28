/**
 * POST /api/browser-gateway/start
 *
 * Proxy to Browser Gateway server: create a new CDP streaming session.
 * Returns session_id, token, and viewer_url.
 * Tailnet-only; no secrets logged.
 */

import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";

const GATEWAY_URL = process.env.BROWSER_GATEWAY_URL || "http://127.0.0.1:8890";

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const resp = await fetch(`${GATEWAY_URL}/session/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        run_id: body.run_id || "manual",
        purpose: body.purpose || "kajabi_login",
        timeout_sec: body.timeout_sec || 3600,
      }),
      signal: AbortSignal.timeout(15000),
    });

    const data = await resp.json();
    return NextResponse.json(data, { status: resp.status });
  } catch (e) {
    return NextResponse.json(
      {
        ok: false,
        error: "Browser Gateway server unreachable",
        error_class: "BROWSER_GATEWAY_UNREACHABLE",
        detail: e instanceof Error ? e.message : "Unknown error",
      },
      { status: 502 },
    );
  }
}
