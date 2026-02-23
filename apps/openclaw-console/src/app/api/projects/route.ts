import { NextRequest, NextResponse } from "next/server";
import { existsSync, readFileSync } from "fs";
import { join } from "path";
import { loadProjectRegistrySafe } from "@/lib/projects";
import { getLastRunForProject } from "@/lib/run-recorder";

type ProjectStateExt = { last_auto_finish_status?: "PASS" | "FAIL"; last_auto_finish_run_id?: string };

function loadProjectStateProjects(): Record<string, ProjectStateExt> {
  const repoRoot = process.env.OPENCLAW_REPO_ROOT || process.cwd();
  const configPath = join(repoRoot, "config", "project_state.json");
  if (!existsSync(configPath)) return {};
  try {
    const data = JSON.parse(readFileSync(configPath, "utf-8"));
    const raw = data.projects ?? {};
    return raw as Record<string, ProjectStateExt>;
  } catch {
    return {};
  }
}

/**
 * GET /api/projects
 *
 * Returns the project registry with last-run status merged in.
 * Protected by token auth (middleware) + origin validation (route).
 * Never leaks secrets. Fail-closed: if registry is invalid, returns error.
 */
export async function GET(req: NextRequest) {
  // CSRF: check same-origin
  const origin = req.headers.get("origin");
  const secFetchSite = req.headers.get("sec-fetch-site");
  if (origin && !origin.includes("127.0.0.1") && !origin.includes("localhost") && secFetchSite !== "same-origin") {
    return NextResponse.json({ ok: false, error: "Forbidden" }, { status: 403 });
  }

  const registry = loadProjectRegistrySafe();
  if (!registry) {
    return NextResponse.json(
      { ok: false, error: "Failed to load project registry. Check config/projects.json." },
      { status: 500 }
    );
  }

  const stateProjects = loadProjectStateProjects();

  // Enrich each project with last run info + project_state (e.g. last_auto_finish_*)
  const enriched = registry.projects.map((project) => {
    const lastRun = getLastRunForProject(project.id);
    const stateProj = stateProjects[project.id];
    return {
      ...project,
      last_run: lastRun
        ? {
            run_id: lastRun.run_id,
            action: lastRun.action,
            status: lastRun.status,
            finished_at: lastRun.finished_at,
            duration_ms: lastRun.duration_ms,
            error_summary: lastRun.error_summary,
          }
        : null,
      last_auto_finish_status: stateProj?.last_auto_finish_status,
      last_auto_finish_run_id: stateProj?.last_auto_finish_run_id,
    };
  });

  return NextResponse.json({ ok: true, projects: enriched });
}
