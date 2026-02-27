import { NextResponse } from "next/server";
import { existsSync, readdirSync, readFileSync } from "fs";
import { join } from "path";
import { execSync } from "child_process";

export const dynamic = "force-dynamic";

/**
 * GET /api/ui/version
 *
 * Returns build_sha + deployed_sha for version certainty.
 * No auth required (public-safe).
 */
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
    const artifactsRoot =
      process.env.OPENCLAW_ARTIFACTS_ROOT ||
      join(process.env.OPENCLAW_REPO_ROOT || process.cwd(), "artifacts");
    const deployDir = join(artifactsRoot, "deploy");
    if (!existsSync(deployDir)) return null;
    const dirs = readdirSync(deployDir)
      .filter((d) => existsSync(join(deployDir, d, "deploy_receipt.json")))
      .sort((a, b) => b.localeCompare(a));
    if (dirs.length === 0) return null;
    const receipt = JSON.parse(
      readFileSync(join(deployDir, dirs[0], "deploy_receipt.json"), "utf-8")
    );
    return receipt.deploy_sha ?? receipt.vps_head ?? null;
  } catch {
    return null;
  }
}

export async function GET() {
  const buildSha = getBuildSha();
  const deploySha = getDeploySha();
  return NextResponse.json({
    build_sha: buildSha,
    deployed_sha: deploySha,
  });
}
