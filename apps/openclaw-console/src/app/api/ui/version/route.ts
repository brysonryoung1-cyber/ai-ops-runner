import { NextResponse } from "next/server";
import { existsSync, readdirSync, readFileSync } from "fs";
import { join } from "path";
import { execSync } from "child_process";

export const dynamic = "force-dynamic";

const DEPLOY_INFO_PATH = "/etc/ai-ops-runner/deploy_info.json";
const SHIP_INFO_PATH = "/etc/ai-ops-runner/ship_info.json";

const GIT_FETCH_TIMEOUT_MS = 8000;
const GIT_REVPARSE_TIMEOUT_MS = 3000;

/** Staleness threshold: ship_info older than this -> drift_status=unknown */
const SHIP_INFO_STALE_DAYS = 7;

/**
 * GET /api/ui/version
 *
 * Returns build_sha, deployed_head_sha, deployed_tree_sha, origin_main_head_sha,
 * origin_main_tree_sha, drift_status, drift, drift_reason, last_deploy_time.
 * Tree-to-tree comparison for drift. Fail-closed: if origin_main_tree_sha cannot
 * be computed (ship_info missing/stale or git unavailable), drift_status=unknown.
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
  deployDir: string | null;
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
      deployDir: null,
    };
  }
  const artifactsRoot =
    process.env.OPENCLAW_ARTIFACTS_ROOT ||
    join(process.env.OPENCLAW_REPO_ROOT || process.cwd(), "artifacts");
  const deployDir = join(artifactsRoot, "deploy");
  if (!existsSync(deployDir))
    return { deployed_head_sha: null, deployed_tree_sha: null, last_deploy_time: null, deployDir: null };
  const dirs = readdirSync(deployDir)
    .filter(
      (d) =>
        existsSync(join(deployDir, d, "deploy_receipt.json")) ||
        existsSync(join(deployDir, d, "deploy_info.json"))
    )
    .sort((a, b) => b.localeCompare(a));
  if (dirs.length === 0)
    return { deployed_head_sha: null, deployed_tree_sha: null, last_deploy_time: null, deployDir: null };
  const latestDir = join(deployDir, dirs[0]);
  const infoData = tryRead(join(latestDir, "deploy_info.json"));
  if (infoData) {
    return {
      deployed_head_sha: infoData.deployed_head_sha ?? infoData.deploy_sha ?? null,
      deployed_tree_sha: infoData.deployed_tree_sha ?? null,
      last_deploy_time: infoData.last_deploy_time ?? infoData.deployed_at ?? null,
      deployDir: latestDir,
    };
  }
  const receipt = tryRead(join(latestDir, "deploy_receipt.json"));
  if (receipt) {
    return {
      deployed_head_sha: receipt.deploy_sha ?? receipt.vps_head ?? null,
      deployed_tree_sha: null,
      last_deploy_time: receipt.deployed_at ?? null,
      deployDir: latestDir,
    };
  }
  return { deployed_head_sha: null, deployed_tree_sha: null, last_deploy_time: null, deployDir: null };
}

interface ShipInfo {
  shipped_head_sha: string | null;
  shipped_tree_sha: string | null;
  shipped_at: string | null;
  source?: string;
}

function getShipInfo(deployDir: string | null): ShipInfo & { stale: boolean } {
  const tryRead = (p: string) => {
    if (!existsSync(p)) return null;
    try {
      return JSON.parse(readFileSync(p, "utf-8"));
    } catch {
      return null;
    }
  };

  let data = tryRead(SHIP_INFO_PATH);
  if (data) {
    const shippedAt = data.shipped_at ?? null;
    const stale = isShipInfoStale(shippedAt);
    return {
      shipped_head_sha: data.shipped_head_sha ?? null,
      shipped_tree_sha: data.shipped_tree_sha ?? null,
      shipped_at: shippedAt,
      source: data.source,
      stale,
    };
  }

  if (deployDir) {
    data = tryRead(join(deployDir, "ship_info.json"));
    if (data) {
      const shippedAt = data.shipped_at ?? null;
      const stale = isShipInfoStale(shippedAt);
      return {
        shipped_head_sha: data.shipped_head_sha ?? null,
        shipped_tree_sha: data.shipped_tree_sha ?? null,
        shipped_at: shippedAt,
        source: data.source,
        stale,
      };
    }
  }

  return {
    shipped_head_sha: null,
    shipped_tree_sha: null,
    shipped_at: null,
    stale: true,
  };
}

function isShipInfoStale(shippedAt: string | null): boolean {
  if (!shippedAt) return true;
  try {
    const shipped = new Date(shippedAt).getTime();
    const now = Date.now();
    const days = (now - shipped) / (24 * 60 * 60 * 1000);
    return days > SHIP_INFO_STALE_DAYS;
  } catch {
    return true;
  }
}

function getOriginMainFromGit(): { head: string | null; tree: string | null } {
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
  const shipInfo = getShipInfo(deployInfo.deployDir);

  const deployedTree = deployInfo.deployed_tree_sha?.slice(0, 40) ?? null;
  const deployedHead = deployInfo.deployed_head_sha?.slice(0, 40) ?? null;

  // Prefer ship_info (no git in container); fallback to git only when ship_info unavailable
  // Only use ship_info when not stale; otherwise treat as unknown
  let originTree: string | null = null;
  let originHead: string | null = null;
  if (!shipInfo.stale && (shipInfo.shipped_tree_sha || shipInfo.shipped_head_sha)) {
    originTree = shipInfo.shipped_tree_sha?.slice(0, 40) ?? null;
    originHead = shipInfo.shipped_head_sha?.slice(0, 40) ?? null;
  }
  if (!originTree && !originHead) {
    const gitOrigin = getOriginMainFromGit();
    originTree = gitOrigin.tree;
    originHead = gitOrigin.head;
  }

  // Fail-closed: if origin_main_tree_sha cannot be computed, drift_status=unknown, never drift=false
  const cannotComputeOrigin = !originTree && !originHead;
  const shipInfoUnusable =
    shipInfo.stale || (!shipInfo.shipped_tree_sha && !shipInfo.shipped_head_sha);

  let driftStatus: "ok" | "unknown" = "ok";
  let drift: boolean | null = null;
  let driftReason: string | null = null;

  if (cannotComputeOrigin || shipInfoUnusable) {
    driftStatus = "unknown";
    drift = null;
    driftReason =
      cannotComputeOrigin && shipInfoUnusable
        ? "origin_main_tree_sha unavailable (ship_info.json missing/stale, git unavailable in container)"
        : shipInfo.stale
          ? `ship_info.json older than ${SHIP_INFO_STALE_DAYS} days`
          : "origin/main refs unavailable";
  } else {
    if (originTree && deployedTree) {
      drift = deployedTree !== originTree;
      driftReason = drift ? "deployed_tree_sha != origin_main_tree_sha" : null;
    } else if (originHead && deployedHead) {
      drift = deployedHead !== originHead;
      driftReason = drift ? "deployed_head_sha != origin_main_head_sha (tree unavailable)" : null;
    } else {
      driftStatus = "unknown";
      drift = null;
      driftReason = "insufficient data for tree comparison";
    }
  }

  return NextResponse.json({
    build_sha: buildSha,
    deployed_head_sha: deployInfo.deployed_head_sha,
    deployed_tree_sha: deployInfo.deployed_tree_sha,
    origin_main_head_sha: originHead,
    origin_main_tree_sha: originTree,
    drift_status: driftStatus,
    drift,
    drift_reason: driftReason,
    last_deploy_time: deployInfo.last_deploy_time,
  });
}
