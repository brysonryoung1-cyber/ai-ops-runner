/**
 * GET /api/agent/status
 *
 * Capability Gate: returns ok/blocked + remediation + links.
 * Aggregates preflight checks, subsystem health, and current state.
 *
 * Fail-closed rule: when a WAITING_FOR_HUMAN soma run is active,
 * browser_gateway MUST be status=ok. If not, overall becomes BLOCKED
 * with remediation instruction. No ambiguous "warn but ok=true".
 *
 * No secrets. Deterministic. No LLM.
 */

import { NextResponse } from "next/server";
import { existsSync, readdirSync, readFileSync } from "fs";
import { join } from "path";
import { resolveSomaLastRun } from "@/lib/soma-last-run-resolver";

export const dynamic = "force-dynamic";

function getRepoRoot(): string {
  return process.env.OPENCLAW_REPO_ROOT || process.cwd();
}

function getArtifactsRoot(): string {
  if (process.env.OPENCLAW_ARTIFACTS_ROOT) return process.env.OPENCLAW_ARTIFACTS_ROOT;
  return join(getRepoRoot(), "artifacts");
}

interface CheckResult {
  status: "ok" | "blocked" | "warn" | "unknown";
  detail: string;
}

async function checkHqHealth(): Promise<CheckResult> {
  const port = process.env.OPENCLAW_CONSOLE_PORT || "8787";
  try {
    const resp = await fetch(`http://127.0.0.1:${port}/api/ui/health_public`, {
      signal: AbortSignal.timeout(3000),
    });
    const data = await resp.json() as Record<string, unknown>;
    return { status: data.ok ? "ok" : "warn", detail: `build_sha=${data.build_sha}` };
  } catch {
    return { status: "blocked", detail: "HQ health_public unreachable" };
  }
}

async function checkHostd(): Promise<CheckResult> {
  const url = process.env.OPENCLAW_HOSTD_URL || "http://127.0.0.1:8877";
  try {
    const resp = await fetch(`${url}/healthz`, { signal: AbortSignal.timeout(3000) });
    if (resp.ok) return { status: "ok", detail: "hostd healthy" };
    return { status: "ok", detail: `hostd reachable (status ${resp.status})` };
  } catch {
    return { status: "blocked", detail: "hostd unreachable" };
  }
}

async function checkBrowserGateway(): Promise<CheckResult> {
  try {
    const resp = await fetch("http://127.0.0.1:8890/health", {
      signal: AbortSignal.timeout(3000),
    });
    const data = await resp.json() as Record<string, unknown>;
    return {
      status: "ok",
      detail: `version=${data.version} uptime_sec=${data.uptime_sec} active_sessions=${data.active_sessions}`,
    };
  } catch {
    return { status: "warn", detail: "Browser Gateway not running" };
  }
}

async function checkDrift(): Promise<CheckResult> {
  const port = process.env.OPENCLAW_CONSOLE_PORT || "8787";
  try {
    const resp = await fetch(`http://127.0.0.1:${port}/api/ui/version`, {
      signal: AbortSignal.timeout(3000),
    });
    const data = await resp.json() as Record<string, unknown>;
    if (data.drift_status === "ok" && !data.drift) {
      return { status: "ok", detail: "drift_status=ok drift=false" };
    }
    return { status: "blocked", detail: `drift_status=${data.drift_status} drift=${data.drift}` };
  } catch {
    return { status: "warn", detail: "Version endpoint unreachable" };
  }
}

function isHumanGateActive(): boolean {
  try {
    const resolved = resolveSomaLastRun();
    return resolved.status === "WAITING_FOR_HUMAN";
  } catch {
    return false;
  }
}

function getLatestPreflight(): Record<string, string> | null {
  const base = join(getArtifactsRoot(), "system", "preflight");
  if (!existsSync(base)) return null;
  const dirs = readdirSync(base).sort().reverse();
  for (const d of dirs.slice(0, 5)) {
    const p = join(base, d, "preflight.json");
    if (existsSync(p)) {
      try {
        return JSON.parse(readFileSync(p, "utf-8"));
      } catch { /* ignore */ }
    }
  }
  return null;
}

function getLatestCanary(): { status: string; run_id: string } | null {
  const base = join(getArtifactsRoot(), "system", "canary");
  if (!existsSync(base)) return null;
  const dirs = readdirSync(base)
    .filter((d) => existsSync(join(base, d, "result.json")))
    .sort().reverse();
  if (dirs.length === 0) return null;
  try {
    const r = JSON.parse(readFileSync(join(base, dirs[0], "result.json"), "utf-8"));
    return { status: r.status ?? "unknown", run_id: dirs[0] };
  } catch {
    return null;
  }
}

export async function GET() {
  const checks: Record<string, CheckResult> = {};

  const [hqHealth, hostd, browserGateway, drift] = await Promise.all([
    checkHqHealth(),
    checkHostd(),
    checkBrowserGateway(),
    checkDrift(),
  ]);
  checks.hq_health = hqHealth;
  checks.hostd = hostd;
  checks.browser_gateway = browserGateway;
  checks.drift = drift;

  const humanGateActive = isHumanGateActive();

  /**
   * Fail-closed: when WAITING_FOR_HUMAN is active and browser_gateway
   * is not ok, escalate from warn to blocked. The human gate requires
   * a working browser gateway to be actionable.
   */
  if (humanGateActive && checks.browser_gateway.status !== "ok") {
    checks.browser_gateway = {
      status: "blocked",
      detail: `${checks.browser_gateway.detail} (BLOCKED: human gate active, browser gateway required)`,
    };
  }

  const latestPreflight = getLatestPreflight();
  const latestCanary = getLatestCanary();

  const blocked = Object.values(checks).some((c) => c.status === "blocked");
  const overall = blocked ? "blocked" : "ok";

  const remediation: string[] = [];
  if (checks.hq_health.status === "blocked") {
    remediation.push("Restart HQ console: systemctl restart openclaw-console");
  }
  if (checks.hostd.status === "blocked") {
    remediation.push("Restart hostd: systemctl restart openclaw-hostd");
  }
  if (checks.drift.status === "blocked") {
    remediation.push("Run deploy to fix drift: ./ops/ship_deploy_verify.sh");
  }
  if (checks.browser_gateway.status === "blocked") {
    remediation.push("Enable Browser Gateway: sudo systemctl enable --now openclaw-browser-gateway.service");
  }
  if (checks.browser_gateway.status === "warn") {
    remediation.push("Browser Gateway not running (optional when no human gate active): sudo systemctl start openclaw-browser-gateway.service");
  }

  return NextResponse.json({
    ok: !blocked,
    overall,
    human_gate_active: humanGateActive,
    checks,
    remediation,
    latest_preflight: latestPreflight
      ? { run_id: (latestPreflight as Record<string, unknown>).run_id, overall: (latestPreflight as Record<string, unknown>).overall }
      : null,
    latest_canary: latestCanary,
    links: {
      inbox: "/inbox",
      soma: "/soma",
      preflight_artifacts: "/artifacts/system/preflight",
      canary_artifacts: "/artifacts/system/canary",
    },
    server_time: new Date().toISOString(),
  });
}
