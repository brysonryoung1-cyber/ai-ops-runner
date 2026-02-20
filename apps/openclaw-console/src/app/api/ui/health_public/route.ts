import { NextResponse } from "next/server";
import { existsSync, readdirSync, readFileSync } from "fs";
import { join } from "path";
import { execSync } from "child_process";

export const dynamic = "force-dynamic";

/**
 * GET /api/ui/health_public
 *
 * Public-safe health endpoint: no HQ token required (exempt in middleware).
 * Returns only non-sensitive fields: build SHA, deploy SHA, canonical URL,
 * route map, and artifacts readability.
 * Used by Settings "Copy UI debug" and external monitoring.
 */

function getArtifactsRoot(): string {
  if (process.env.OPENCLAW_ARTIFACTS_ROOT) return process.env.OPENCLAW_ARTIFACTS_ROOT;
  const repo = process.env.OPENCLAW_REPO_ROOT || process.cwd();
  return join(repo, "artifacts");
}

function getBuildSha(): string {
  if (process.env.OPENCLAW_BUILD_SHA) return process.env.OPENCLAW_BUILD_SHA;
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

function getDeploySha(): string | null {
  try {
    const artifactsRoot = getArtifactsRoot();
    const deployDir = join(artifactsRoot, "deploy");
    if (!existsSync(deployDir)) return null;
    const dirs = readdirSync(deployDir)
      .filter((d) => existsSync(join(deployDir, d, "deploy_receipt.json")))
      .sort((a, b) => b.localeCompare(a));
    if (dirs.length === 0) return null;
    const receipt = JSON.parse(readFileSync(join(deployDir, dirs[0], "deploy_receipt.json"), "utf-8"));
    return receipt.deploy_sha ?? receipt.vps_head ?? null;
  } catch {
    return null;
  }
}

function getCanonicalUrl(): string | null {
  return process.env.OPENCLAW_CANONICAL_URL || null;
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
  let buildSha = getBuildSha();
  const deploySha = getDeploySha();
  if (buildSha === "unknown" && deploySha) {
    buildSha = deploySha;
  }
  const canonicalUrl = getCanonicalUrl();
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
    deploy_sha: deploySha,
    canonical_url: canonicalUrl,
    routes: CONSOLE_ROUTES,
    artifacts: {
      readable: artifactsReadable,
      dir_count: artifactDirCount,
    },
    server_time: new Date().toISOString(),
  });
}
