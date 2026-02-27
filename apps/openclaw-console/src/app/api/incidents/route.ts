/**
 * GET /api/incidents
 *
 * List recent incidents from artifacts/incidents/.
 * Returns incident_id, status, summary excerpt, artifact paths.
 * No secrets. Redact sensitive fields.
 */

import { NextRequest, NextResponse } from "next/server";
import { existsSync, readdirSync, readFileSync } from "fs";
import { join } from "path";

export const dynamic = "force-dynamic";

function getArtifactsRoot(): string {
  if (process.env.OPENCLAW_ARTIFACTS_ROOT) return process.env.OPENCLAW_ARTIFACTS_ROOT;
  const repo = process.env.OPENCLAW_REPO_ROOT || process.cwd();
  return join(repo, "artifacts");
}

export async function GET(req: NextRequest) {
  const limit = Math.min(parseInt(req.nextUrl.searchParams.get("limit") || "20", 10), 50);
  const base = join(getArtifactsRoot(), "incidents");
  if (!existsSync(base)) {
    return NextResponse.json({ ok: true, incidents: [] });
  }
  const dirs = readdirSync(base, { withFileTypes: true })
    .filter((e) => e.isDirectory())
    .map((e) => e.name)
    .sort()
    .reverse()
    .slice(0, limit);

  const incidents: { incident_id: string; status?: string; summary?: string; artifact_dir: string }[] = [];
  for (const id of dirs) {
    const incDir = join(base, id);
    let status: string | undefined;
    let summary: string | undefined;
    const summaryPath = join(incDir, "SUMMARY.md");
    if (existsSync(summaryPath)) {
      try {
        const raw = readFileSync(summaryPath, "utf-8");
        const statusMatch = raw.match(/\*\*Status:\*\*\s*(\S+)/);
        status = statusMatch?.[1];
        const lines = raw.split("\n").filter((l) => l.trim() && !l.startsWith("#"));
        summary = lines.slice(0, 3).join(" ").slice(0, 200);
      } catch {
        // ignore
      }
    }
    incidents.push({
      incident_id: id,
      status,
      summary,
      artifact_dir: `artifacts/incidents/${id}`,
    });
  }
  return NextResponse.json({ ok: true, incidents });
}
