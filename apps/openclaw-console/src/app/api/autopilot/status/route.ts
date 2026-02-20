import { NextResponse } from "next/server";
import { existsSync, readFileSync } from "fs";
import { join } from "path";
import { executeAction } from "@/lib/hostd";

export const dynamic = "force-dynamic";

/**
 * GET /api/autopilot/status
 *
 * Public-safe autopilot status endpoint. No token required (exempt in middleware).
 * Reads state from the autopilot state directory on the host filesystem.
 *
 * Auto-migration: if autopilot is installed but not enabled, and admin_token_loaded
 * and trust_tailscale are true, we call hostd autopilot_enable once (per process)
 * so existing installs "heal" into enabled without manual UI action.
 */

/** One-shot migration guard: only attempt auto-enable once per process. */
let autopilotMigrationAttempted = false;

function getStateDir(): string {
  return process.env.OPENCLAW_AUTOPILOT_STATE_DIR || "/var/lib/ai-ops-runner/autopilot";
}

function readStateFile(filename: string): string | null {
  try {
    const path = join(getStateDir(), filename);
    if (!existsSync(path)) return null;
    return readFileSync(path, "utf-8").trim() || null;
  } catch {
    return null;
  }
}

function readJsonFile(filename: string): Record<string, unknown> | null {
  try {
    const raw = readStateFile(filename);
    if (!raw) return null;
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function readStatus(stateDir: string) {
  const enabled = existsSync(join(stateDir, "enabled"));
  const lastDeployedSha = readStateFile("last_deployed_sha.txt");
  const lastGoodSha = readStateFile("last_good_sha.txt");
  const failCountRaw = readStateFile("fail_count.txt");
  const failCount = failCountRaw ? parseInt(failCountRaw, 10) : 0;
  const lastRun = readJsonFile("last_run.json");
  const timerInterval = process.env.OPENCLAW_AUTOPILOT_INTERVAL || "5min";
  const stateExists = existsSync(stateDir);
  return {
    stateExists,
    enabled,
    lastDeployedSha,
    lastGoodSha,
    failCount,
    lastRun,
    timerInterval,
  };
}

export async function GET() {
  const stateDir = getStateDir();
  let s = readStatus(stateDir);

  // Auto-migration: installed but not enabled + admin token + trust tailscale → enable once via hostd
  const adminTokenLoaded =
    typeof process.env.OPENCLAW_ADMIN_TOKEN === "string" &&
    process.env.OPENCLAW_ADMIN_TOKEN.length > 0;
  const trustTailscale = process.env.OPENCLAW_TRUST_TAILSCALE === "1";
  if (
    s.stateExists &&
    !s.enabled &&
    adminTokenLoaded &&
    trustTailscale &&
    !autopilotMigrationAttempted
  ) {
    autopilotMigrationAttempted = true;
    try {
      await executeAction("autopilot_enable");
      // Re-read state (hostd may have created enabled sentinel on host; if state dir is mounted we see it)
      s = readStatus(stateDir);
    } catch {
      // Non-fatal; status still returned below
    }
  }

  return NextResponse.json({
    ok: true,
    installed: s.stateExists,
    enabled: s.enabled,
    interval: s.timerInterval,
    last_deployed_sha: s.lastDeployedSha,
    last_good_sha: s.lastGoodSha,
    fail_count: s.failCount,
    last_run: s.lastRun,
    last_error:
      s.lastRun && (s.lastRun as Record<string, unknown>).error_class
        ? (s.lastRun as Record<string, unknown>).error_class
        : null,
    last_run_id: s.lastRun ? (s.lastRun as Record<string, unknown>).run_id : null,
    server_time: new Date().toISOString(),
  });
}
