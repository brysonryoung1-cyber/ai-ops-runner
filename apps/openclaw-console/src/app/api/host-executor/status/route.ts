import { NextRequest, NextResponse } from "next/server";
import { join } from "path";
import { existsSync, readFileSync, writeFileSync, mkdirSync } from "fs";
import { checkConnectivity, getHostdUrl } from "@/lib/hostd";

const STATUS_TIMEOUT_MS = 3000;

/** Derive console network mode from OPENCLAW_HOSTD_URL (no secrets). */
function getConsoleNetworkMode(): "host" | "bridge" | "unknown" {
  const url = process.env.OPENCLAW_HOSTD_URL ?? "";
  if (url.includes("host.docker.internal") || url.includes("host-gateway")) return "bridge";
  if (url.includes("127.0.0.1") || url.includes("localhost")) return "host";
  return "unknown";
}

/** Redact/normalize URL for display (strip trailing slash, no tokens). */
function normalizeExecutorUrl(url: string | null): string | null {
  if (!url) return null;
  return url.replace(/\/$/, "").replace(/[?&#].*$/, "");
}

function getProbeStatePath(): string {
  const root = process.env.OPENCLAW_ARTIFACTS_ROOT || join(process.env.OPENCLAW_REPO_ROOT || process.cwd(), "artifacts");
  return join(root, ".host_executor_probe_state.json");
}

interface ProbeState {
  last_success_at: string | null;
  last_failure_at: string | null;
  last_error: string | null;
}

function readProbeState(): ProbeState {
  try {
    const path = getProbeStatePath();
    if (existsSync(path)) {
      const raw = readFileSync(path, "utf-8");
      const data = JSON.parse(raw) as Partial<ProbeState>;
      return {
        last_success_at: data.last_success_at ?? null,
        last_failure_at: data.last_failure_at ?? null,
        last_error: data.last_error ?? null,
      };
    }
  } catch {
    // ignore
  }
  return { last_success_at: null, last_failure_at: null, last_error: null };
}

function writeProbeState(ok: boolean, error: string | null): void {
  try {
    const path = getProbeStatePath();
    const dir = join(path, "..");
    mkdirSync(dir, { recursive: true });
    const now = new Date().toISOString();
    const prev = readProbeState();
    const state: ProbeState = {
      last_success_at: ok ? now : prev.last_success_at,
      last_failure_at: ok ? prev.last_failure_at : now,
      last_error: ok ? null : (error ?? prev.last_error),
    };
    writeFileSync(path, JSON.stringify(state, null, 2) + "\n", "utf-8");
  } catch {
    // best-effort
  }
}

/**
 * GET /api/host-executor/status
 *
 * Server-mediated Host Executor (hostd) connectivity check.
 * HQ server proxies to hostd (localhost on aiops-1); UI must NEVER call hostd directly.
 *
 * Returns: ok, latency_ms, executor_url (no secrets), last_success_at, last_failure_at,
 * last_error (redacted), error_class?, message_redacted?.
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

  const executorUrlRaw = getHostdUrl() ?? null;
  const executorUrl = normalizeExecutorUrl(executorUrlRaw);
  const state = readProbeState();
  const consoleNetworkMode = getConsoleNetworkMode();

  try {
    const result = await Promise.race([
      checkConnectivity(),
      new Promise<never>((_, reject) =>
        setTimeout(() => reject(new Error("Host Executor check timed out")), STATUS_TIMEOUT_MS)
      ),
    ]);

    const messageRedacted = result.ok
      ? undefined
      : (result.error ?? "").replace(/https?:\/\/[^\s]+/g, "[URL_REDACTED]");
    writeProbeState(result.ok, result.error ?? null);
    const now = new Date().toISOString();

    const payload: {
      ok: boolean;
      latency_ms: number;
      executor_url: string | null;
      console_can_reach_hostd: boolean;
      console_network_mode: string;
      last_success_at: string | null;
      last_failure_at: string | null;
      last_error_redacted: string | null;
      error_class?: string;
      message_redacted?: string;
    } = {
      ok: result.ok,
      latency_ms: result.durationMs ?? 0,
      executor_url: executorUrl,
      console_can_reach_hostd: result.ok,
      console_network_mode: consoleNetworkMode,
      last_success_at: result.ok ? now : state.last_success_at,
      last_failure_at: result.ok ? state.last_failure_at : now,
      last_error_redacted: result.ok ? null : ((result.error ?? state.last_error) ?? "").replace(/https?:\/\/[^\s]+/g, "[URL_REDACTED]") || null,
    };
    if (!result.ok && result.error) {
      payload.error_class = "HOSTD_UNREACHABLE";
      payload.message_redacted = messageRedacted;
    }
    return NextResponse.json(payload, { status: result.ok ? 200 : 502 });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    writeProbeState(false, msg);
    return NextResponse.json(
      {
        ok: false,
        latency_ms: STATUS_TIMEOUT_MS,
        executor_url: executorUrl,
        console_can_reach_hostd: false,
        console_network_mode: consoleNetworkMode,
        last_success_at: state.last_success_at,
        last_failure_at: new Date().toISOString(),
        last_error_redacted: msg.includes("timed out") ? "Host Executor unreachable (timeout)" : "[REDACTED]",
        error_class: "HOSTD_TIMEOUT",
        message_redacted: msg.includes("timed out") ? "Host Executor unreachable (timeout)" : "[REDACTED]",
      },
      { status: 502 }
    );
  }
}
