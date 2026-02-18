import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";

function requireAdminToken(req: NextRequest): NextResponse | null {
  const adminToken = process.env.OPENCLAW_ADMIN_TOKEN;
  if (!adminToken || typeof adminToken !== "string") {
    return NextResponse.json(
      { ok: false, error_class: "ADMIN_NOT_CONFIGURED", error: "Admin token not configured." },
      { status: 503 }
    );
  }
  const provided =
    req.headers.get("X-OpenClaw-Admin-Token") ?? req.headers.get("X-OpenClaw-Token") ?? "";
  if (provided.length < 8 || provided !== adminToken) {
    return NextResponse.json(
      { ok: false, error_class: "FORBIDDEN", error: "Admin token required." },
      { status: 403 }
    );
  }
  return null;
}

/**
 * GET /api/connectors/gmail/secret-status
 *
 * Proxies to hostd GET /connectors/gmail/secret-status.
 * Returns { exists: boolean, fingerprint: string | null }. Admin-gated.
 */
export async function GET(req: NextRequest) {
  const adminError = requireAdminToken(req);
  if (adminError) return adminError;

  const hostdUrl = process.env.OPENCLAW_HOSTD_URL;
  if (!hostdUrl || !hostdUrl.startsWith("http")) {
    return NextResponse.json(
      { ok: false, error_class: "HOSTD_UNREACHABLE", error: "Host Executor URL not configured." },
      { status: 502 }
    );
  }

  const adminToken = process.env.OPENCLAW_ADMIN_TOKEN!;
  try {
    const res = await fetch(`${hostdUrl.replace(/\/$/, "")}/connectors/gmail/secret-status`, {
      method: "GET",
      headers: { "X-OpenClaw-Admin-Token": adminToken },
      signal: AbortSignal.timeout(5000),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      return NextResponse.json(
        { ok: false, error: (data as { error?: string }).error ?? `HTTP ${res.status}` },
        { status: res.status >= 400 && res.status < 600 ? res.status : 502 }
      );
    }
    return NextResponse.json(data);
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return NextResponse.json(
      { ok: false, error_class: "HOSTD_UNREACHABLE", error: message },
      { status: 502 }
    );
  }
}
