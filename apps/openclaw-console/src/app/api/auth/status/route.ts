import { NextRequest, NextResponse } from "next/server";
import { existsSync, readFileSync, readdirSync } from "fs";
import { join } from "path";
import { execSync } from "child_process";

export const dynamic = "force-dynamic";

/**
 * GET /api/auth/status
 *
 * Self-diagnosing auth endpoint. Returns booleans + build/deploy SHAs + canonical
 * URL so production users can debug 403s without screenshots or log spelunking.
 *
 * Never leaks secrets: only booleans and masked token fingerprints.
 *
 * Exempt from HQ token auth when OPENCLAW_TRUST_TAILSCALE=1 (see middleware).
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
    const deployDir = join(getArtifactsRoot(), "deploy");
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

function maskFingerprint(token: string | undefined): string | null {
  if (!token || token.length < 8) return null;
  return `${token.slice(0, 4)}…${token.slice(-4)}`;
}

async function checkHostExecutor(): Promise<boolean> {
  const url = process.env.OPENCLAW_HOSTD_URL;
  if (!url) return false;
  try {
    const res = await fetch(`${url.replace(/\/$/, "")}/health`, {
      method: "GET",
      signal: AbortSignal.timeout(2500),
    });
    if (!res.ok) return false;
    const data = await res.json().catch(() => ({}));
    return data?.ok === true;
  } catch {
    return false;
  }
}

const UI_ROUTES = [
  "/",
  "/projects",
  "/runs",
  "/artifacts",
  "/actions",
  "/settings",
  "/soma",
];

export async function GET(req: NextRequest) {
  const consoleToken = process.env.OPENCLAW_CONSOLE_TOKEN;
  const adminToken = process.env.OPENCLAW_ADMIN_TOKEN;
  const trustTailscale = process.env.OPENCLAW_TRUST_TAILSCALE === "1";

  const hqTokenRequired = !!consoleToken && !trustTailscale;
  const adminTokenLoaded = typeof adminToken === "string" && adminToken.length > 0;
  const hostExecutorReachable = await checkHostExecutor();

  const notes: string[] = [];

  if (!consoleToken) {
    notes.push("OPENCLAW_CONSOLE_TOKEN not set — auth is bypassed (first-time setup mode).");
  }
  if (trustTailscale) {
    notes.push("OPENCLAW_TRUST_TAILSCALE=1 — HQ token gate bypassed for browser requests (Tailscale membership is access control).");
  }
  if (!adminTokenLoaded) {
    notes.push("OPENCLAW_ADMIN_TOKEN not loaded — host executor admin actions (deploy_and_verify) will be blocked.");
  }
  if (!hostExecutorReachable) {
    notes.push("Host Executor (hostd) is unreachable — connector and workflow actions will fail.");
  }
  if (hostExecutorReachable && adminTokenLoaded) {
    notes.push("All systems nominal.");
  }

  return NextResponse.json({
    ok: true,
    hq_token_required: hqTokenRequired,
    admin_token_loaded: adminTokenLoaded,
    host_executor_reachable: hostExecutorReachable,
    build_sha: getBuildSha(),
    deploy_sha: getDeploySha(),
    canonical_url: process.env.OPENCLAW_CANONICAL_URL || null,
    ui_routes: UI_ROUTES,
    trust_tailscale: trustTailscale,
    console_token_fingerprint: consoleToken ? maskFingerprint(consoleToken) : null,
    notes,
  });
}
