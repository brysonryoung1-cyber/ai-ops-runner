import { NextRequest, NextResponse } from "next/server";
import { checkConnectivity } from "@/lib/hostd";

const STATUS_TIMEOUT_MS = 3000;

/**
 * GET /api/host-executor/status
 *
 * Server-mediated Host Executor (hostd) connectivity check.
 * HQ server proxies to hostd (localhost on aiops-1); UI must NEVER call hostd directly.
 *
 * Returns { ok, latency_ms, error_class?, message_redacted? }.
 * Enforces hard timeout (3s) to prevent infinite "Checking…".
 */
function validateOrigin(req: NextRequest): NextResponse | null {
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
  if (origin && allowed.has(origin)) return null;
  if (secFetchSite === "same-origin") return null;
  if (!origin && (host.startsWith("127.0.0.1") || host.startsWith("localhost"))) return null;
  return NextResponse.json({ ok: false, error: "Forbidden" }, { status: 403 });
}

export async function GET(req: NextRequest) {
  const originError = validateOrigin(req);
  if (originError) return originError;

  try {
    const result = await Promise.race([
      checkConnectivity(),
      new Promise<never>((_, reject) =>
        setTimeout(() => reject(new Error("Host Executor check timed out")), STATUS_TIMEOUT_MS)
      ),
    ]);

    const payload: {
      ok: boolean;
      latency_ms: number;
      error_class?: string;
      message_redacted?: string;
    } = {
      ok: result.ok,
      latency_ms: result.durationMs ?? 0,
    };
    if (!result.ok && result.error) {
      payload.error_class = "HOSTD_UNREACHABLE";
      payload.message_redacted = result.error.replace(/https?:\/\/[^\s]+/g, "[URL_REDACTED]");
    }
    return NextResponse.json(payload, { status: result.ok ? 200 : 502 });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return NextResponse.json(
      {
        ok: false,
        latency_ms: STATUS_TIMEOUT_MS,
        error_class: "HOSTD_TIMEOUT",
        message_redacted: msg.includes("timed out") ? "Host Executor unreachable (timeout)" : "[REDACTED]",
      },
      { status: 502 }
    );
  }
}
