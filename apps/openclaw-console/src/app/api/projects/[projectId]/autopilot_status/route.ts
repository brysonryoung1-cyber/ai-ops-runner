/**
 * GET /api/projects/[projectId]/autopilot_status
 *
 * Soma Autopilot status: enabled/disabled, last tick, last run_id, current status, links.
 * Only implemented for projectId=soma_kajabi.
 */

import { NextRequest, NextResponse } from "next/server";
import { existsSync, readdirSync, readFileSync } from "fs";
import { join } from "path";
import { resolveSomaLastRun } from "@/lib/soma-last-run-resolver";

export const runtime = "nodejs";

function getStateDir(): string {
  const env = process.env.OPENCLAW_SOMA_AUTOPILOT_STATE_DIR;
  if (env) return env;
  const repo = process.env.OPENCLAW_REPO_ROOT || process.cwd();
  const defaultPath = join(repo, "artifacts", "soma_kajabi", ".autopilot_state");
  const etcPath = "/var/lib/ai-ops-runner/soma_autopilot";
  return existsSync(etcPath) ? etcPath : defaultPath;
}

function getArtifactsRoot(): string {
  if (process.env.OPENCLAW_ARTIFACTS_ROOT) return process.env.OPENCLAW_ARTIFACTS_ROOT;
  const repo = process.env.OPENCLAW_REPO_ROOT || process.cwd();
  return join(repo, "artifacts");
}

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ projectId: string }> }
) {
  const { projectId } = await params;
  if (projectId !== "soma_kajabi") {
    return NextResponse.json(
      { ok: false, error: "Autopilot status only available for soma_kajabi" },
      { status: 404 }
    );
  }

  const artifactsRoot = getArtifactsRoot();
  const autopilotRoot = join(artifactsRoot, "soma_kajabi", "autopilot");

  const stateDir = getStateDir();
  let enabled = false;
  try {
    const enabledFile = join(stateDir, "enabled.txt");
    if (existsSync(enabledFile)) {
      enabled = readFileSync(enabledFile, "utf-8").trim() === "1";
    }
  } catch {
    // State dir may not exist or not mounted
  }

  let blocked = false;
  let failCount = 0;
  try {
    blocked = existsSync(join(stateDir, "blocked"));
    const fcPath = join(stateDir, "infra_fail_count.txt");
    if (existsSync(fcPath)) {
      const fc = readFileSync(fcPath, "utf-8").trim();
      failCount = parseInt(fc, 10) || 0;
    }
  } catch {
    // State dir may not exist
  }

  // Latest status artifact
  let lastTick: string | null = null;
  let lastRunId: string | null = null;
  let currentStatus: string | null = null;
  let outcome: string | null = null;
  let statusPath: string | null = null;
  let errorClass: string | null = null;

  if (existsSync(autopilotRoot)) {
    const dirs = readdirSync(autopilotRoot, { withFileTypes: true })
      .filter((e) => e.isDirectory())
      .map((e) => e.name)
      .sort()
      .reverse();
    for (const d of dirs.slice(0, 5)) {
      const statusJson = join(autopilotRoot, d, "status.json");
      if (existsSync(statusJson)) {
        try {
          const data = JSON.parse(readFileSync(statusJson, "utf-8"));
          lastTick = data.timestamp ?? d;
          lastRunId = data.run_id ?? null;
          currentStatus = data.current_status ?? data.outcome ?? null;
          outcome = data.outcome ?? null;
          errorClass = data.error_class ?? null;
          statusPath = `artifacts/soma_kajabi/autopilot/${d}/status.json`;
          break;
        } catch {
          continue;
        }
      }
    }
  }

  // When WAITING_FOR_HUMAN, include novnc_url + instruction_line from resolver
  let novncUrl: string | null = null;
  let instructionLine: string | null = null;
  let artifactDir: string | null = null;
  if (currentStatus === "WAITING_FOR_HUMAN") {
    const resolved = resolveSomaLastRun();
    novncUrl = resolved.novnc_url;
    instructionLine = resolved.instruction_line;
    artifactDir = resolved.artifact_dir;
  }

  return NextResponse.json({
    ok: true,
    enabled,
    blocked,
    fail_count: failCount,
    last_tick: lastTick,
    last_run_id: lastRunId,
    current_status: currentStatus,
    outcome,
    error_class: errorClass,
    status_path: statusPath,
    timer_interval: "10min",
    novnc_url: novncUrl,
    instruction_line: instructionLine,
    artifact_dir: artifactDir,
  });
}
