import { NextRequest, NextResponse } from "next/server";

/**
 * GET /api/auth/context
 *
 * Returns auth context for the UI (e.g. isAdmin for Deploy+Verify visibility).
 * Fail-closed: only OPENCLAW_ADMIN_TOKEN (when set) gets isAdmin: true.
 * If OPENCLAW_ADMIN_TOKEN is unset, no one is admin (isAdmin: false).
 * No secrets in response.
 */
export async function GET(req: NextRequest) {
  const origin = req.headers.get("origin");
  const secFetchSite = req.headers.get("sec-fetch-site");
  if (
    origin &&
    !origin.includes("127.0.0.1") &&
    !origin.includes("localhost") &&
    secFetchSite !== "same-origin"
  ) {
    return NextResponse.json({ ok: false, error: "Forbidden" }, { status: 403 });
  }

  const adminToken = process.env.OPENCLAW_ADMIN_TOKEN;
  const provided = req.headers.get("x-openclaw-token") ?? "";

  const isAdmin =
    typeof adminToken === "string" &&
    adminToken.length > 0 &&
    provided !== "" &&
    provided === adminToken;

  return NextResponse.json({ ok: true, isAdmin });
}
