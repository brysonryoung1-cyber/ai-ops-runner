import { NextResponse } from "next/server";
import { existsSync, readdirSync, readFileSync } from "fs";
import { join } from "path";
import { execSync } from "child_process";

export const dynamic = "force-dynamic";

const DEPLOY_INFO_PATH = "/etc/ai-ops-runner/deploy_info.json";

const GIT_FETCH_TIMEOUT_MS = 8000;
const GIT_REVPARSE_TIMEOUT_MS = 3000;

/**
 * GET /api/ui/version
 *
 * Returns build_sha, deployed_head_sha, deployed_tree_sha, origin_main_head_sha,
 * origin_main_tree_sha, drift (boolean), drift_reason, last_deploy_time.
 * Tree-to-tree comparison for drift. No auth required (public-safe).
 */
function getBuildSha(): string {
  if (process.env.OPENCLAW_BUILD_SHA) return process.env.OPENCLAW_BUILD_SHA;
  try {
    const cwd = process.env.OPENCLAW_REPO_ROOT || process.cwd();
    return execSync("git rev-parse --short HEAD", {
      encoding: "utf-8",
      cwd,
      timeout: GIT_REVPARSE_TIMEOUT_MS,
    }).trim();
  } catch {
    return "unknown";
  }
}

function getDeployInfo(): {
  deployed_head_sha: string | null;
  deployed_tree_sha: string | null;
  last_deploy_time: string | null;
} {
  const tryRead = (p: string) => {
    if (!existsSync(p)) return null;
    try {
      return JSON.parse(readFileSync(p, "utf-8"));
    } catch {
      return null;
    }
  };
  const data = tryRead(DEPLOY_INFO_PATH);
  if (data) {
    return {
      deployed_head_sha: data.deployed_head_sha ?? data.deploy_sha ?? null,
      deployed_tree_sha: data.deployed_tree_sha ?? null,
      last_deploy_time: data.last_deploy_time ?? data.deployed_at ?? null,
    };
  }
  const artifactsRoot =
    process.env.OPENCLAW_ARTIFACTS_ROOT ||
    join(process.env.OPENCLAW_REPO_ROOT || process.cwd(), "artifacts");
  const deployDir = join(artifactsRoot, "deploy");
  if (!existsSync(deployDir)) return { deployed_head_sha: null, deployed_tree_sha: null, last_deploy_time: null };
  const dirs = readdirSync(deployDir)
    .filter((d) => existsSync(join(deployDir, d, "deploy_receipt.json")) || existsSync(join(deployDir, d, "deploy_info.json")))
    .sort((a, b) => b.localeCompare(a));
  if (dirs.length === 0) return { deployed_head_sha: null, deployed_tree_sha: null, last_deploy_time: null };
  const latestDir = join(deployDir, dirs[0]);
  const infoData = tryRead(join(latestDir, "deploy_info.json"));
  if (infoData) {
    return {
      deployed_head_sha: infoData.deployed_head_sha ?? infoData.deploy_sha ?? null,
      deployed_tree_sha: infoData.deployed_tree_sha ?? null,
      last_deploy_time: infoData.last_deploy_time ?? infoData.deployed_at ?? null,
    };
  }
  const receipt = tryRead(join(latestDir, "deploy_receipt.json"));
  if (receipt) {
    return {
      deployed_head_sha: receipt.deploy_sha ?? receipt.vps_head ?? null,
      deployed_tree_sha: null,
      last_deploy_time: receipt.deployed_at ?? null,
    };
  }
  return { deployed_head_sha: null, deployed_tree_sha: null, last_deploy_time: null };
}

function getOriginMainInfo(): { head: string | null; tree: string | null } {
  const cwd = process.env.OPENCLAW_REPO_ROOT || process.cwd();
  if (!existsSync(join(cwd, ".git"))) return { head: null, tree: null };
  try {
    execSync("git fetch origin main 2>/dev/null || git fetch origin 2>/dev/null", {
      encoding: "utf-8",
      cwd,
      timeout: GIT_FETCH_TIMEOUT_MS,
    });
  } catch {
    // fetch may fail (offline, no remote); continue with cached refs
  }
  try {
    const head = execSync("git rev-parse origin/main 2>/dev/null", {
      encoding: "utf-8",
      cwd,
      timeout: GIT_REVPARSE_TIMEOUT_MS,
    })
      .trim()
      .slice(0, 40) || null;
    const tree = execSync("git rev-parse origin/main^{tree} 2>/dev/null", {
      encoding: "utf-8",
      cwd,
      timeout: GIT_REVPARSE_TIMEOUT_MS,
    })
      .trim()
      .slice(0, 40) || null;
    return { head, tree };
  } catch {
    return { head: null, tree: null };
  }
}

export async function GET() {
  const buildSha = getBuildSha();
  const deployInfo = getDeployInfo();
  const origin = getOriginMainInfo();

  const deployedTree = deployInfo.deployed_tree_sha?.slice(0, 40) ?? null;
  const deployedHead = deployInfo.deployed_head_sha?.slice(0, 40) ?? null;
  const originTree = origin.tree;
  const originHead = origin.head;

  let drift = false;
  let driftReason = "";

  if (originTree && deployedTree) {
    drift = deployedTree !== originTree;
    driftReason = drift ? "deployed_tree_sha != origin_main_tree_sha" : "";
  } else if (originHead && deployedHead) {
    drift = deployedHead !== originHead;
    driftReason = drift ? "deployed_head_sha != origin_main_head_sha (tree unavailable)" : "";
  } else {
    drift = !deployedTree && !deployedHead;
    driftReason = !deployedTree && !deployedHead
      ? "deploy_info missing or incomplete"
      : !originTree && !originHead
        ? "origin/main refs unavailable"
        : "insufficient data for tree comparison";
  }

  return NextResponse.json({
    build_sha: buildSha,
    deployed_head_sha: deployInfo.deployed_head_sha,
    deployed_tree_sha: deployInfo.deployed_tree_sha,
    origin_main_head_sha: originHead,
    origin_main_tree_sha: originTree,
    drift,
    drift_reason: driftReason || null,
    last_deploy_time: deployInfo.last_deploy_time,
  });
}
