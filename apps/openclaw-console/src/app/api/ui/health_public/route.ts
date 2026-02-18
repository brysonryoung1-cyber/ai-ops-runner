import { NextResponse } from "next/server";
import { existsSync, readdirSync } from "fs";
import { join } from "path";
import { execSync } from "child_process";

export const dynamic = "force-dynamic";

/**
 * GET /api/ui/health_public
 *
 * Public-safe health endpoint: no HQ token required (exempt in middleware).
 * Returns only non-sensitive fields: build SHA, route map, artifacts readable.
 * Used by Settings "Copy UI debug" and external monitoring.
 */

function getArtifactsRoot(): string {
  if (process.env.OPENCLAW_ARTIFACTS_ROOT) return process.env.OPENCLAW_ARTIFACTS_ROOT;
  const repo = process.env.OPENCLAW_REPO_ROOT || process.cwd();
  return join(repo, "artifacts");
}

function getBuildSha(): string {
  try {
    const cwd = process.env.OPENCLAW_REPO_ROOT || process.cwd();
    return execSync("git rev-parse --short HEAD", {
      encoding: "utf-8",
      cwd,
      timeout: 3000,
    }).trim();
  } catch {
    return "unknown";
  }
}

const CONSOLE_ROUTES = [
  "/",
  "/projects",
  "/projects/[projectId]",
  "/runs",
  "/artifacts",
  "/artifacts/[...path]",
  "/actions",
  "/settings",
  "/soma",
];

export async function GET() {
  const buildSha = getBuildSha();
  const artifactsRoot = getArtifactsRoot();
  let artifactsReadable = false;
  let artifactDirCount = 0;

  try {
    if (existsSync(artifactsRoot)) {
      const entries = readdirSync(artifactsRoot, { withFileTypes: true });
      artifactsReadable = true;
      artifactDirCount = entries.filter((e) => e.isDirectory()).length;
    }
  } catch {
    artifactsReadable = false;
  }

  return NextResponse.json({
    ok: true,
    build_sha: buildSha,
    routes: CONSOLE_ROUTES,
    artifacts: {
      readable: artifactsReadable,
      dir_count: artifactDirCount,
    },
    server_time: new Date().toISOString(),
  });
}
