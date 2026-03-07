import { NextRequest, NextResponse } from "next/server";
import { deriveActor } from "@/lib/audit";
import { readAutonomyMode, writeAutonomyMode } from "@/lib/autonomy-mode";

export const runtime = "nodejs";

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
  const state = readAutonomyMode();
  return NextResponse.json({
    ok: true,
    mode: state.mode,
    updated_at: state.updated_at,
    updated_by: state.updated_by,
    path: state.path,
  });
}

export async function POST(req: NextRequest) {
  const originError = validateOrigin(req);
  if (originError) return originError;

  let body: { mode?: string };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ ok: false, error: "Invalid JSON body." }, { status: 400 });
  }

  const mode = body.mode === "OFF" ? "OFF" : body.mode === "ON" ? "ON" : null;
  if (!mode) {
    return NextResponse.json(
      { ok: false, error: 'Body must include mode="ON" or mode="OFF".' },
      { status: 400 }
    );
  }

  const actor = deriveActor(req.headers.get("x-openclaw-token"));
  const state = writeAutonomyMode(mode, actor);
  return NextResponse.json({
    ok: true,
    mode: state.mode,
    updated_at: state.updated_at,
    updated_by: state.updated_by,
    path: state.path,
  });
}
