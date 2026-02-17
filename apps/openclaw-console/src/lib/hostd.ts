/**
 * Host Executor (hostd) client — no SSH.
 * Calls OPENCLAW_HOSTD_URL for health and exec. Auth via X-OpenClaw-Admin-Token.
 * Fail-closed: no URL or no token for admin actions => clear errors.
 */

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
}

const ACTION_TO_HOSTD: Record<string, string> = {
  doctor: "doctor",
  apply: "apply",
  deploy_and_verify: "deploy_and_verify",
  ports: "port_audit",
  journal: "tail_guard_log",
  timer: "timer",
  guard: "guard",
  llm_doctor: "llm_doctor",
  soma_snapshot_home: "soma_snapshot_home",
  soma_snapshot_practitioner: "soma_snapshot_practitioner",
  soma_harvest: "soma_harvest",
  soma_mirror: "soma_mirror",
  soma_status: "soma_status",
  soma_last_errors: "soma_last_errors",
  sms_status: "sms_status",
  artifacts: "artifacts",
};

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
    let data: { ok?: boolean; error?: string; stdout?: string; stderr?: string; exitCode?: number; truncated?: boolean; artifact_dir?: string };
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
      return {
        ok: false,
        action: actionName,
        stdout: data.stdout ?? "",
        stderr: data.stderr ?? "",
        exitCode: null,
        durationMs,
        error: data.error ?? `HTTP ${res.status}`,
      };
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

/**
 * Quick connectivity check: GET hostd /health (no token).
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

  try {
    const res = await fetch(`${baseUrl}/health`, {
      method: "GET",
      signal: AbortSignal.timeout(5000),
    });
    const durationMs = Date.now() - start;
    if (!res.ok) {
      return {
        ok: false,
        error: `Hostd health returned ${res.status}`,
        durationMs,
      };
    }
    const data = await res.json().catch(() => ({}));
    return {
      ok: data?.ok === true,
      error: data?.ok === true ? undefined : "Health check did not return ok",
      durationMs,
    };
  } catch (err) {
    const durationMs = Date.now() - start;
    const message = err instanceof Error ? err.message : String(err);
    return {
      ok: false,
      error: `Host Executor unreachable: ${message}`,
      durationMs,
    };
  }
}
