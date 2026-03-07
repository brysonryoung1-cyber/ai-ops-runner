import { NextRequest, NextResponse } from "next/server";
import { listApprovals } from "@/lib/approvals";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function validateOrigin(req: NextRequest): NextResponse | null {
  const port = process.env.OPENCLAW_CONSOLE_PORT || process.env.PORT || "8787";
  const allowed = new Set([
    `http://127.0.0.1:${port}`,
    `http://localhost:${port}`,
  ]);
  if (process.env.OPENCLAW_TAILSCALE_HOSTNAME) {
    allowed.add(`https://${process.env.OPENCLAW_TAILSCALE_HOSTNAME}`);
  }
  const origin = req.headers.get("origin");
  const secFetchSite = req.headers.get("sec-fetch-site");
  const host = req.headers.get("host") ?? "";
  if (origin && allowed.has(origin)) return null;
  if (secFetchSite === "same-origin") return null;
  if (!origin && (host.startsWith("127.0.0.1") || host.startsWith("localhost"))) return null;
  return NextResponse.json({ ok: false, error: "Forbidden" }, { status: 403 });
}

export async function GET(req: NextRequest) {
  const originError = validateOrigin(req);
  if (originError) return originError;
  const projectId = req.nextUrl.searchParams.get("project_id") || undefined;
  const status = req.nextUrl.searchParams.get("status");
  const approvals = listApprovals({
    projectId,
    status: status === "APPROVED" || status === "REJECTED" || status === "PENDING" ? status : "ALL",
    limit: 200,
  });
  return NextResponse.json({
    ok: true,
    approvals,
  });
}
