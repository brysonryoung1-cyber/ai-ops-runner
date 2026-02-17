import { NextRequest, NextResponse } from "next/server";
import { readFileSync, existsSync, readdirSync, statSync } from "fs";
import { join } from "path";

/**
 * GET /api/project/state
 *
 * Returns project state (canonical brain): config/project_state.json
 * plus latest artifacts/state snapshot metadata. No secrets.
 * Protected by token auth (middleware).
 */

interface ProjectState {
  project_name?: string;
  goal_summary?: string;
  last_verified_vps_head?: string | null;
  last_deploy_timestamp?: string | null;
  last_guard_result?: string | null;
  last_doctor_result?: string | null;
  llm_primary_provider?: string;
  llm_primary_model?: string;
  llm_fallback_provider?: string;
  llm_fallback_model?: string;
  zane_agent_phase?: number;
  next_action_id?: string | null;
  next_action_text?: string | null;
  ui_accepted?: boolean | null;
  ui_accepted_at?: string | null;
  ui_accepted_commit?: string | null;
}

interface ProjectStateResponse {
  ok: boolean;
  state: ProjectState;
  latest_snapshot?: { timestamp: string; path: string } | null;
}

async function getProjectStateFromRunner(): Promise<ProjectStateResponse | null> {
  const runnerUrl = process.env.RUNNER_API_URL;
  if (!runnerUrl) return null;
  try {
    const url = `${runnerUrl.replace(/\/$/, "")}/project/state`;
    const res = await fetch(url, { signal: AbortSignal.timeout(8000) });
    const data = await res.json();
    if (data?.ok && data?.state) {
      return {
        ok: true,
        state: data.state,
        latest_snapshot: data.latest_snapshot ?? null,
      };
    }
  } catch {
    // fall through to file-based
  }
  return null;
}

function getProjectStateFromFiles(): ProjectStateResponse {
  const repoRoot = process.env.OPENCLAW_REPO_ROOT || process.cwd();
  const configPath = join(repoRoot, "config", "project_state.json");
  let state: ProjectState = {};
  if (existsSync(configPath)) {
    try {
      state = JSON.parse(readFileSync(configPath, "utf-8"));
    } catch {
      // leave state empty
    }
  }
  let latest_snapshot: { timestamp: string; path: string } | null = null;
  const stateDir = join(repoRoot, "artifacts", "state");
  if (existsSync(stateDir)) {
    try {
      const subdirs = readdirSync(stateDir).filter((d) => {
        const p = join(stateDir, d);
        return statSync(p).isDirectory();
      });
      if (subdirs.length > 0) {
        const latestTs = subdirs.sort()[subdirs.length - 1];
        const snapshotPath = join(stateDir, latestTs, "state.json");
        if (existsSync(snapshotPath)) {
          latest_snapshot = { timestamp: latestTs, path: `artifacts/state/${latestTs}/state.json` };
        }
      }
    } catch {
      // leave latest_snapshot null
    }
  }
  return { ok: true, state, latest_snapshot };
}

export async function GET(req: NextRequest) {
  const origin = req.headers.get("origin");
  const secFetchSite = req.headers.get("sec-fetch-site");
  if (
    origin &&
    !origin.includes("127.0.0.1") &&
    !origin.includes("localhost") &&
    secFetchSite !== "same-origin"
  ) {
    return NextResponse.json(
      { ok: false, error: "Forbidden" },
      { status: 403 }
    );
  }

  const fromRunner = await getProjectStateFromRunner();
  const response = fromRunner ?? getProjectStateFromFiles();
  return NextResponse.json(response);
}
