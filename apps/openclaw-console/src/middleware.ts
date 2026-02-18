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
 * Tailscale-trusted mode: When OPENCLAW_TRUST_TAILSCALE=1, the HQ token
 * gate is bypassed for browser→HQ requests. Tailnet membership is the
 * access control. Admin-token requirement for hostd admin actions is
 * still enforced server-side (not in middleware).
 *
 * Session TTL: The token itself is the session. Rotate it periodically
 * via `python3 ops/openclaw_console_token.py rotate`. The TTL is
 * enforced by short-lived tokens rather than server-side sessions,
 * keeping the server stateless.
 *
 * Security events are logged as single-line entries (no secrets).
 */

/** Maximum request body size for API routes (1MB) */
const MAX_BODY_SIZE = 1024 * 1024;

/**
 * Routes exempt from HQ token auth. These endpoints return only
 * non-sensitive diagnostic data and have their own origin validation.
 */
const TOKEN_EXEMPT_ROUTES = new Set([
  "/api/sms",
  "/api/auth/status",
  "/api/ui/health_public",
]);

export function middleware(req: NextRequest) {
  const path = req.nextUrl.pathname;

  // Exempt specific routes from token auth (each has its own security)
  if (TOKEN_EXEMPT_ROUTES.has(path)) {
    return NextResponse.next();
  }

  const token = process.env.OPENCLAW_CONSOLE_TOKEN;

  // No token configured → skip auth (origin validation still active in routes)
  if (!token) {
    return NextResponse.next();
  }

  // Tailscale-trusted mode: bypass HQ token gate when enabled.
  // Admin-token requirement for host executor admin actions is still
  // enforced server-side in the route handlers (not here).
  if (process.env.OPENCLAW_TRUST_TAILSCALE === "1") {
    return NextResponse.next();
  }

  const provided = req.headers.get("x-openclaw-token");

  if (provided !== token) {
    const tokenStatus = provided ? "invalid" : "missing";
    const ip = req.headers.get("x-forwarded-for") || "unknown";
    // Single-line security event — no secrets logged
    console.error(
      `[SECURITY] Unauthorized API access: path=${path} token=${tokenStatus} ip=${ip}`
    );

    return NextResponse.json(
      {
        ok: false,
        error: "Unauthorized: missing or invalid X-OpenClaw-Token header.",
        error_class: "HQ_TOKEN_MISSING",
      },
      { status: 401 }
    );
  }

  // Check Content-Length to prevent oversized payloads
  const contentLength = req.headers.get("content-length");
  if (contentLength && parseInt(contentLength, 10) > MAX_BODY_SIZE) {
    return NextResponse.json(
      { ok: false, error: "Request body too large." },
      { status: 413 }
    );
  }

  return NextResponse.next();
}

export const config = {
  matcher: "/api/:path*",
};
