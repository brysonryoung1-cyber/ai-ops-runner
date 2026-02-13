import { NextRequest, NextResponse } from "next/server";

/**
 * Middleware: Token-based authentication for all /api/* routes.
 *
 * If OPENCLAW_CONSOLE_TOKEN is set (loaded from Keychain by start.sh),
 * every API request must include a matching X-OpenClaw-Token header.
 *
 * If no token is configured, auth is bypassed (graceful degradation
 * for first-time setup). CSRF/origin validation is still enforced
 * in the route handlers as a second layer.
 *
 * Security events are logged as single-line entries (no secrets).
 */
export function middleware(req: NextRequest) {
  const token = process.env.OPENCLAW_CONSOLE_TOKEN;

  // No token configured → skip auth (origin validation still active in routes)
  if (!token) {
    return NextResponse.next();
  }

  const provided = req.headers.get("x-openclaw-token");

  if (provided !== token) {
    const path = req.nextUrl.pathname;
    const tokenStatus = provided ? "invalid" : "missing";
    // Single-line security event — no secrets logged
    console.error(
      `[SECURITY] Unauthorized API access: path=${path} token=${tokenStatus}`
    );

    return NextResponse.json(
      {
        ok: false,
        error:
          "Unauthorized: missing or invalid X-OpenClaw-Token header.",
      },
      { status: 401 }
    );
  }

  return NextResponse.next();
}

export const config = {
  matcher: "/api/:path*",
};
