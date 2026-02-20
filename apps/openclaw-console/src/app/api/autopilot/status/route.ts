import { NextResponse } from "next/server";
import { existsSync, readFileSync } from "fs";
import { join } from "path";

export const dynamic = "force-dynamic";

/**
 * GET /api/autopilot/status
 *
 * Public-safe autopilot status endpoint. No token required (exempt in middleware).
 * Reads state from the autopilot state directory on the host filesystem.
 */

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

export async function GET() {
  const stateDir = getStateDir();
  const enabled = existsSync(join(stateDir, "enabled"));
  const lastDeployedSha = readStateFile("last_deployed_sha.txt");
  const lastGoodSha = readStateFile("last_good_sha.txt");
  const failCountRaw = readStateFile("fail_count.txt");
  const failCount = failCountRaw ? parseInt(failCountRaw, 10) : 0;
  const lastRun = readJsonFile("last_run.json");

  const timerInterval = process.env.OPENCLAW_AUTOPILOT_INTERVAL || "5min";
  const stateExists = existsSync(stateDir);

  return NextResponse.json({
    ok: true,
    installed: stateExists,
    enabled,
    interval: timerInterval,
    last_deployed_sha: lastDeployedSha,
    last_good_sha: lastGoodSha,
    fail_count: failCount,
    last_run: lastRun,
    last_error: lastRun && (lastRun as Record<string, unknown>).error_class
      ? (lastRun as Record<string, unknown>).error_class
      : null,
    last_run_id: lastRun ? (lastRun as Record<string, unknown>).run_id : null,
    server_time: new Date().toISOString(),
  });
}
