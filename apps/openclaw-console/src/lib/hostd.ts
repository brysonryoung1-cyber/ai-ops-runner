/**
 * Host Executor (hostd) client — no SSH.
 * Calls OPENCLAW_HOSTD_URL for health and exec. Auth via X-OpenClaw-Admin-Token.
 * Fail-closed: no URL or no token for admin actions => clear errors.
 * ACTION_TO_HOSTD from config/action_registry.json (single source of truth).
 */

import { ACTION_TO_HOSTD } from "./action_registry.generated";

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

function getHostdUrl(): string | null {
  const url = process.env.OPENCLAW_HOSTD_URL;
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

  try {
    const res = await fetch(`${baseUrl}/exec`, {
      method: "POST",
      headers,
      body: JSON.stringify({ action: hostdAction }),
      signal: AbortSignal.timeout(920_000), // slightly over max hostd timeout
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

const HEALTH_RETRIES = 3;
const HEALTH_RETRY_DELAY_MS = 500;

/**
 * Quick connectivity check: GET hostd /health (no token).
 * Retries up to HEALTH_RETRIES with backoff to avoid false "unreachable" on transient timeouts.
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
        signal: AbortSignal.timeout(5000),
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
