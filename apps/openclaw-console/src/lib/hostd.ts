/**
 * Host Executor (hostd) client — no SSH.
 * Calls OPENCLAW_HOSTD_URL for health and exec. Auth via X-OpenClaw-Admin-Token.
 * Fail-closed: no URL or no token for admin actions => clear errors.
 * ACTION_TO_HOSTD from config/action_registry.json (single source of truth).
 *
 * Long-running actions (reauth, auto_finish, capture_interactive) use extended
 * timeouts to avoid undici bodyTimeout (default 300s) causing "fetch failed".
 * OPENCLAW_HOSTD_EXEC_TIMEOUT_MS overrides; 0 = no AbortSignal limit.
 */

import { Agent, fetch as undiciFetch } from "undici";
import { ACTION_TO_HOSTD } from "./action_registry.generated";

/** Actions that may run >5 min; use extended exec timeout. Exported for async exec routing. */
export const LONG_RUNNING_ACTIONS = new Set([
  "soma_kajabi_reauth_and_resume",
  "soma_kajabi_auto_finish",
  "soma_run_to_done",
  "soma_fix_and_retry",
  "soma_kajabi_capture_interactive",
  "soma_kajabi_session_check",
  "soma_kajabi_unblock_and_run",
  "deploy_and_verify",
]);

export interface HostdResult {
  ok: boolean;
  action: string;
  stdout: string;
  stderr: string;
  exitCode: number | null;
  durationMs: number;
  error?: string;
  artifact_dir?: string;
  truncated?: boolean;
  /** When hostd returns 423 Locked (Soma-first gate). */
  httpStatus?: number;
  error_class?: string;
  required_condition?: string;
}

export const HOSTD_ACTIONS = Object.keys(ACTION_TO_HOSTD);

function buildMockStdout(actionName: string): string {
  const payloads: Record<string, Record<string, unknown>> = {
    soma_connectors_status: {
      result_summary: { kajabi: "not_connected", gmail: "not_connected" },
    },
    soma_kajabi_bootstrap_start: {
      result_summary: "Bootstrap started (mock)",
      next_steps: {
        instruction: "Check status and finalize when ready.",
        verification_url: null,
        user_code: null,
      },
    },
    soma_kajabi_bootstrap_status: {
      result_summary: { status: "pending", ready_to_finalize: false },
    },
    soma_kajabi_bootstrap_finalize: {
      result_summary: "Bootstrap finalized (mock)",
    },
    soma_kajabi_gmail_connect_start: {
      result_summary: "Gmail connect started (mock)",
      next_steps: {
        instruction: "Complete OAuth in browser; then refresh status.",
        verification_url: "https://example.com/device",
        user_code: "MOCK-CODE",
      },
    },
    soma_kajabi_gmail_connect_status: {
      result_summary: { status: "pending", ready_to_finalize: false },
    },
    soma_kajabi_gmail_connect_finalize: {
      result_summary: "Gmail connect finalized (mock)",
    },
  };
  const payload = payloads[actionName] ?? {
    result_summary: "Mock hostd ok",
  };
  return JSON.stringify(payload);
}

function mockHostdResult(actionName: string): HostdResult {
  const safeAction = actionName.replace(/[^a-zA-Z0-9_.-]/g, "_");
  return {
    ok: true,
    action: actionName,
    stdout: buildMockStdout(actionName),
    stderr: "",
    exitCode: 0,
    durationMs: 5,
    artifact_dir: `artifacts/hostd/mock-${safeAction}`,
    truncated: false,
    httpStatus: 200,
  };
}

/** Exported for fail-fast error messages and status UI (no secrets; localhost or host.docker.internal). */
export function getHostdUrl(): string | null {
  const url = process.env.OPENCLAW_HOSTD_URL ?? "http://127.0.0.1:8877";
  if (!url || typeof url !== "string" || !url.startsWith("http")) return null;
  return url.replace(/\/$/, "");
}

/**
 * Execute an allowlisted action via hostd. Returns result compatible with former SSH result shape.
 */
export async function executeAction(actionName: string): Promise<HostdResult> {
  const start = Date.now();
  const hostdAction = ACTION_TO_HOSTD[actionName];
  if (!hostdAction) {
    return {
      ok: false,
      action: actionName,
      stdout: "",
      stderr: "",
      exitCode: null,
      durationMs: Date.now() - start,
      error: `Action "${actionName}" is not available via Host Executor.`,
    };
  }

  if (process.env.OPENCLAW_HOSTD_MOCK === "1") {
    return mockHostdResult(actionName);
  }

  const baseUrl = getHostdUrl();
  if (!baseUrl) {
    return {
      ok: false,
      action: actionName,
      stdout: "",
      stderr: "",
      exitCode: null,
      durationMs: Date.now() - start,
      error:
        "Host Executor not configured. Set OPENCLAW_HOSTD_URL (e.g. http://host.docker.internal:8877).",
    };
  }

  const adminToken = process.env.OPENCLAW_ADMIN_TOKEN;
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (adminToken) {
    headers["X-OpenClaw-Admin-Token"] = adminToken;
  }

  // Resolve exec timeout: env override, or 60m for long-running, 60s for normal.
  // Undici bodyTimeout (default 300s) kills long requests; use Agent with bodyTimeout: 0.
  const envMs = process.env.OPENCLAW_HOSTD_EXEC_TIMEOUT_MS;
  const execTimeoutMs = envMs
    ? Math.max(0, parseInt(envMs, 10) || 60_000)
    : LONG_RUNNING_ACTIONS.has(actionName)
      ? 3_600_000 // 60 min for reauth, auto_finish, capture_interactive, etc.
      : 60_000;   // 60 s for normal actions

  const agent = new Agent({ bodyTimeout: 0, headersTimeout: 0 }); // Disable undici defaults (300s each)
  const signal = execTimeoutMs > 0 ? AbortSignal.timeout(execTimeoutMs) : undefined;

  try {
    const res = await undiciFetch(`${baseUrl}/exec`, {
      method: "POST",
      headers,
      body: JSON.stringify({ action: hostdAction }),
      signal,
      dispatcher: agent,
    });
    const durationMs = Date.now() - start;
    const text = await res.text();
    let data: {
      ok?: boolean;
      error?: string;
      stdout?: string;
      stderr?: string;
      exitCode?: number;
      truncated?: boolean;
      artifact_dir?: string;
      error_class?: string;
      required_condition?: string;
    };
    try {
      data = JSON.parse(text);
    } catch {
      return {
        ok: false,
        action: actionName,
        stdout: "",
        stderr: text.slice(0, 500),
        exitCode: null,
        durationMs,
        error: `Hostd returned non-JSON (${res.status}).`,
      };
    }

    if (!res.ok) {
      const payload = {
        ok: false,
        action: actionName,
        stdout: data.stdout ?? "",
        stderr: data.stderr ?? "",
        exitCode: null,
        durationMs,
        error: data.error ?? `HTTP ${res.status}`,
        httpStatus: res.status,
        error_class: data.error_class,
        required_condition: data.required_condition,
      };
      return payload;
    }

    return {
      ok: data.ok === true,
      action: actionName,
      stdout: data.stdout ?? "",
      stderr: data.stderr ?? "",
      exitCode: data.exitCode ?? null,
      durationMs,
      artifact_dir: data.artifact_dir,
      truncated: data.truncated,
      httpStatus: res.status,
      error_class: data.error_class,
      required_condition: data.required_condition,
    };
  } catch (err) {
    const durationMs = Date.now() - start;
    const message = err instanceof Error ? err.message : String(err);
    return {
      ok: false,
      action: actionName,
      stdout: "",
      stderr: "",
      exitCode: null,
      durationMs,
      error: `Host Executor unreachable: ${message}`,
    };
  }
}

const HEALTH_RETRIES = 2;
const HEALTH_RETRY_DELAY_MS = 300;
const HEALTH_TIMEOUT_MS = 2500;

/**
 * Quick connectivity check: GET hostd /health (no token).
 * Uses short timeout to avoid infinite "Checking…" in UI.
 */
export async function checkConnectivity(): Promise<{
  ok: boolean;
  error?: string;
  durationMs: number;
}> {
  const start = Date.now();
  const baseUrl = getHostdUrl();
  if (!baseUrl) {
    return {
      ok: false,
      error: "OPENCLAW_HOSTD_URL not set. Host Executor (localhost) required.",
      durationMs: Date.now() - start,
    };
  }

  let lastError: string | undefined;
  for (let attempt = 1; attempt <= HEALTH_RETRIES; attempt++) {
    try {
      const res = await fetch(`${baseUrl}/health`, {
        method: "GET",
        signal: AbortSignal.timeout(HEALTH_TIMEOUT_MS),
      });
      if (!res.ok) {
        lastError = `Hostd health returned ${res.status}`;
        if (attempt < HEALTH_RETRIES) await new Promise((r) => setTimeout(r, HEALTH_RETRY_DELAY_MS));
        continue;
      }
      const data = await res.json().catch(() => ({}));
      if (data?.ok === true) {
        return { ok: true, durationMs: Date.now() - start };
      }
      lastError = "Health check did not return ok";
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      lastError = `Host Executor unreachable: ${message}`;
    }
    if (attempt < HEALTH_RETRIES) {
      await new Promise((r) => setTimeout(r, HEALTH_RETRY_DELAY_MS));
    }
  }
  return {
    ok: false,
    error: lastError,
    durationMs: Date.now() - start,
  };
}
