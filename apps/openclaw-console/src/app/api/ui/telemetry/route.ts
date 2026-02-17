import { NextRequest, NextResponse } from "next/server";
import { writeFileSync, mkdirSync } from "fs";
import { join } from "path";
import { createHash } from "crypto";

const MAX_DETAIL_LENGTH = 500;
const REDACT_PATTERNS = [
  /token[\s:=]+[\w-]+/gi,
  /password[\s:=]+[^\s]+/gi,
  /api[_-]?key[\s:=]+[\w-]+/gi,
  /bearer\s+[\w.-]+/gi,
  /[\w.-]+@[\w.-]+\.\w+/g,
];

function redact(s: string): string {
  let out = s.slice(0, MAX_DETAIL_LENGTH);
  for (const re of REDACT_PATTERNS) {
    out = out.replace(re, "[REDACTED]");
  }
  return out;
}

function resolveArtifactsRoot(): string {
  const repoRoot = process.env.OPENCLAW_REPO_ROOT || process.cwd();
  const candidates = [
    join(repoRoot, "artifacts"),
    join(process.cwd(), "..", "..", "artifacts"),
  ];
  return candidates[0];
}

/**
 * POST /api/ui/telemetry
 * Body: { event: "click" | "error", page: string, control?: string, detail?: string, ts?: string }
 *
 * Writes artifacts/ui_telemetry/<run_id>/event.json and SUMMARY.md.
 * Payload is truncated and redacted; no secrets in artifacts.
 */
export async function POST(req: NextRequest) {
  const origin = req.headers.get("origin");
  const secFetchSite = req.headers.get("sec-fetch-site");
  const host = req.headers.get("host") ?? "";
  const port = process.env.OPENCLAW_CONSOLE_PORT || process.env.PORT || "8787";
  const allowed = new Set([
    `http://127.0.0.1:${port}`,
    `http://localhost:${port}`,
  ]);
  if (process.env.OPENCLAW_TAILSCALE_HOSTNAME) {
    allowed.add(`https://${process.env.OPENCLAW_TAILSCALE_HOSTNAME}`);
  }
  const allowedOrigin = origin && allowed.has(origin);
  const sameOrigin = secFetchSite === "same-origin";
  const localhostNoOrigin = !origin && (host.startsWith("127.0.0.1") || host.startsWith("localhost"));
  if (!allowedOrigin && !sameOrigin && !localhostNoOrigin) {
    return NextResponse.json({ ok: false, error: "Forbidden" }, { status: 403 });
  }

  let body: { event?: string; page?: string; control?: string; detail?: string; ts?: string };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ ok: false, error: "Invalid JSON" }, { status: 400 });
  }
  if (!body || typeof body !== "object") {
    return NextResponse.json({ ok: false, error: "Body must be object" }, { status: 400 });
  }

  const event = body.event === "click" || body.event === "error" ? body.event : "click";
  const page = typeof body.page === "string" ? body.page.slice(0, 200) : "";
  const control = typeof body.control === "string" ? body.control.slice(0, 200) : "";
  const rawDetail = typeof body.detail === "string" ? body.detail : "";
  const detail = redact(rawDetail);
  const ts = typeof body.ts === "string" ? body.ts : new Date().toISOString();

  const runId = `ui-${Date.now()}-${createHash("sha256").update(ts + page + control).digest("hex").slice(0, 8)}`;
  const base = join(resolveArtifactsRoot(), "ui_telemetry", runId);

  try {
    mkdirSync(base, { recursive: true });
    const eventPayload = { event, page, control, detail, ts };
    writeFileSync(join(base, "event.json"), JSON.stringify(eventPayload, null, 2) + "\n", "utf-8");
    const summary = `# UI Telemetry ${runId}\n\nEvent: ${event}\nPage: ${page}\nControl: ${control}\nTS: ${ts}\n`;
    writeFileSync(join(base, "SUMMARY.md"), summary, "utf-8");
    return NextResponse.json({ ok: true, run_id: runId });
  } catch (err) {
    console.error("[telemetry] write failed:", err);
    return NextResponse.json({ ok: false, error: "Write failed" }, { status: 500 });
  }
}
