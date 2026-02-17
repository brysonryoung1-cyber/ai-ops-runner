import { NextRequest, NextResponse } from "next/server";
import { existsSync, readdirSync } from "fs";
import { join } from "path";
import { execSync } from "child_process";

function getArtifactsRoot(): string {
  if (process.env.OPENCLAW_ARTIFACTS_ROOT) return process.env.OPENCLAW_ARTIFACTS_ROOT;
  const repo = process.env.OPENCLAW_REPO_ROOT || process.cwd();
  return join(repo, "artifacts");
}

function getBuildSha(): string {
  try {
    return execSync("git rev-parse --short HEAD", {
      encoding: "utf-8",
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
  "/logs",
  "/settings",
  "/soma",
];

/**
 * GET /api/ui/health
 *
 * Returns build SHA, console route map, and a quick check
 * that the artifacts root is readable. No secrets.
 */
const ALLOWED_ORIGINS = new Set([
  "http://127.0.0.1:3000",
  "http://127.0.0.1:8787",
  "http://localhost:3000",
  "http://localhost:8787",
]);

export async function GET(req: NextRequest) {
  const origin = req.headers.get("origin");
  const secFetchSite = req.headers.get("sec-fetch-site");
  if (origin && !ALLOWED_ORIGINS.has(origin) && secFetchSite !== "same-origin") {
    return NextResponse.json({ ok: false, error: "Forbidden" }, { status: 403 });
  }
  const host = req.headers.get("host") ?? "";
  if (!origin && secFetchSite !== "same-origin") {
    const allowedHost = host === "127.0.0.1:3000" || host === "127.0.0.1:8787" || host === "localhost:3000" || host === "localhost:8787";
    if (!allowedHost) {
      return NextResponse.json({ ok: false, error: "Forbidden" }, { status: 403 });
    }
  }

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
      root: artifactsRoot,
      readable: artifactsReadable,
      dir_count: artifactDirCount,
    },
    server_time: new Date().toISOString(),
    node_version: process.version,
  });
}
