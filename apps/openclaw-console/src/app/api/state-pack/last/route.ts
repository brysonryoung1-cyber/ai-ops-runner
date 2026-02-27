/**
 * GET /api/state-pack/last
 *
 * Returns the most recent State Pack run_id and link.
 */

import { NextResponse } from "next/server";
import { existsSync, readdirSync } from "fs";
import { join } from "path";

export const dynamic = "force-dynamic";

function getArtifactsRoot(): string {
  if (process.env.OPENCLAW_ARTIFACTS_ROOT) return process.env.OPENCLAW_ARTIFACTS_ROOT;
  const repo = process.env.OPENCLAW_REPO_ROOT || process.cwd();
  return join(repo, "artifacts");
}

export async function GET() {
  const base = join(getArtifactsRoot(), "system", "state_pack");
  if (!existsSync(base)) {
    return NextResponse.json({ ok: true, run_id: null, artifact_dir: null });
  }
  const dirs = readdirSync(base, { withFileTypes: true })
    .filter((e) => e.isDirectory())
    .map((e) => e.name)
    .sort()
    .reverse();
  const runId = dirs[0] ?? null;
  return NextResponse.json({
    ok: true,
    run_id: runId,
    artifact_dir: runId ? `artifacts/system/state_pack/${runId}` : null,
  });
}
