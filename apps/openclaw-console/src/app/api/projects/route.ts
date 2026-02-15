import { NextRequest, NextResponse } from "next/server";
import { loadProjectRegistrySafe } from "@/lib/projects";
import { getLastRunForProject } from "@/lib/run-recorder";

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

  // Enrich each project with last run info
  const enriched = registry.projects.map((project) => {
    const lastRun = getLastRunForProject(project.id);
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
    };
  });

  return NextResponse.json({ ok: true, projects: enriched });
}
