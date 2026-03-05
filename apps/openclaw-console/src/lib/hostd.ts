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
import { existsSync, readFileSync } from "fs";
import { ACTION_TO_HOSTD } from "./action_registry.generated";

/** Actions that may run >5 min; use extended exec timeout. Exported for async exec routing. */
export const LONG_RUNNING_ACTIONS = new Set([
  "soma_kajabi_reauth_and_resume",
  "soma_kajabi_auto_finish",
  "soma_run_to_done",
  "soma_fix_and_retry",
  "soma_novnc_oneclick_recovery",
  "soma_kajabi_capture_interactive",
  "soma_kajabi_session_check",
  "soma_kajabi_unblock_and_run",
  "deploy_and_verify",
  "system.reconcile",
  "code.opencode.propose_patch",
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

export interface HostdAdminTokenResolution {
  token: string | null;
  source: "env" | "file" | "env_file" | "missing";
  source_detail: string;
  evidence: string[];
}

export const HOSTD_ACTIONS = Object.keys(ACTION_TO_HOSTD);

const HOSTD_ADMIN_TOKEN_FILE = "/etc/ai-ops-runner/secrets/openclaw_admin_token";
const HOSTD_ENV_FILE = "/etc/ai-ops-runner/secrets/openclaw_hostd.env";
const HOSTD_ADMIN_TOKEN_EVIDENCE = [
  "env OPENCLAW_ADMIN_TOKEN",
  `file ${HOSTD_ADMIN_TOKEN_FILE}`,
  `file ${HOSTD_ENV_FILE}: OPENCLAW_ADMIN_TOKEN`,
] as const;

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

function safeReadFile(path: string): string | null {
  try {
    if (!existsSync(path)) return null;
    return readFileSync(path, "utf-8");
  } catch {
    return null;
  }
}

function parseEnvFileValue(raw: string, key: string): string | null {
  for (const line of raw.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const match = trimmed.match(/^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$/);
    if (!match || match[1] !== key) continue;
    let value = match[2].trim();
    if (
      (value.startsWith("\"") && value.endsWith("\"")) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }
    return value.trim() || null;
  }
  return null;
}

export function resolveHostdAdminToken(): HostdAdminTokenResolution {
  const envToken = process.env.OPENCLAW_ADMIN_TOKEN?.trim();
  if (envToken) {
    return {
      token: envToken,
      source: "env",
      source_detail: "OPENCLAW_ADMIN_TOKEN",
      evidence: [...HOSTD_ADMIN_TOKEN_EVIDENCE],
    };
  }

  const fileToken = safeReadFile(HOSTD_ADMIN_TOKEN_FILE)?.trim();
  if (fileToken) {
    return {
      token: fileToken,
      source: "file",
      source_detail: HOSTD_ADMIN_TOKEN_FILE,
      evidence: [...HOSTD_ADMIN_TOKEN_EVIDENCE],
    };
  }

  const envFileToken = parseEnvFileValue(
    safeReadFile(HOSTD_ENV_FILE) ?? "",
    "OPENCLAW_ADMIN_TOKEN"
  );
  if (envFileToken) {
    return {
      token: envFileToken,
      source: "env_file",
      source_detail: `${HOSTD_ENV_FILE}:OPENCLAW_ADMIN_TOKEN`,
      evidence: [...HOSTD_ADMIN_TOKEN_EVIDENCE],
    };
  }

  return {
    token: null,
    source: "missing",
    source_detail: "unresolved",
    evidence: [...HOSTD_ADMIN_TOKEN_EVIDENCE],
  };
}

function buildHostdAuthMissingSummary(evidence: readonly string[]): string {
  return `HOSTD_AUTH_MISSING: console could not resolve hostd admin token; checked ${evidence.join(", ")}`;
}

function normalizeHostdErrorPayload(
  status: number,
  data: {
    error?: string;
    error_class?: string;
    required_header?: string;
    auth_source?: string;
    token_sources_checked?: string[];
  },
  auth: HostdAdminTokenResolution
): { error: string; error_class?: string } {
  const isAuthForbidden =
    status === 403 &&
    (
      data.error === "Forbidden" ||
      data.error_class === "HOSTD_FORBIDDEN" ||
      typeof data.required_header === "string" ||
      typeof data.auth_source === "string"
    );
  const errorClass = data.error_class
    ?? (isAuthForbidden ? "HOSTD_FORBIDDEN" : undefined)
    ?? (status === 503 && data.error === "admin not configured" ? "HOSTD_AUTH_MISSING" : undefined);

  if (errorClass === "HOSTD_AUTH_MISSING") {
    const evidence = Array.isArray(data.token_sources_checked) && data.token_sources_checked.length > 0
      ? data.token_sources_checked
      : auth.evidence;
    return {
      error: buildHostdAuthMissingSummary(evidence),
      error_class: errorClass,
    };
  }

  if (errorClass === "HOSTD_FORBIDDEN") {
    const header = data.required_header ?? "X-OpenClaw-Admin-Token";
    const tokenSource = data.auth_source ?? auth.source_detail;
    const reason =
      typeof data.error === "string" && data.error.trim() && data.error !== "Forbidden"
        ? data.error.trim()
        : "hostd rejected the admin token";
    return {
      error: `HOSTD_FORBIDDEN: ${reason}; header=${header}; token_source=${tokenSource}`,
      error_class: errorClass,
    };
  }

  return {
    error: data.error ?? `HTTP ${status}`,
    error_class: errorClass,
  };
}

/**
 * Execute an allowlisted action via hostd. Returns result compatible with former SSH result shape.
 * For code.opencode.propose_patch, pass params { goal, ref?, test_command?, dry_run? }.
 */
export async function executeAction(
  actionName: string,
  params?: Record<string, unknown>,
  consoleRunId?: string
): Promise<HostdResult> {
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

  const adminToken = resolveHostdAdminToken();
  if (!adminToken.token) {
    return {
      ok: false,
      action: actionName,
      stdout: "",
      stderr: "",
      exitCode: null,
      durationMs: Date.now() - start,
      error: buildHostdAuthMissingSummary(adminToken.evidence),
      error_class: "HOSTD_AUTH_MISSING",
    };
  }
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  headers["X-OpenClaw-Admin-Token"] = adminToken.token;

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

  const body: Record<string, unknown> = { action: hostdAction };
  if (params && actionName === "code.opencode.propose_patch") {
    body.params = params;
  }
  if (typeof consoleRunId === "string" && consoleRunId.trim().length > 0) {
    body.console_run_id = consoleRunId.trim();
  }

  try {
    const res = await undiciFetch(`${baseUrl}/exec`, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
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
      const normalizedError = normalizeHostdErrorPayload(res.status, data, adminToken);
      const payload = {
        ok: false,
        action: actionName,
        stdout: data.stdout ?? "",
        stderr: data.stderr ?? "",
        exitCode: null,
        durationMs,
        error: normalizedError.error,
        httpStatus: res.status,
        error_class: normalizedError.error_class,
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
