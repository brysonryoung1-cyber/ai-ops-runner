import { NextRequest, NextResponse } from "next/server";
import { readdirSync, readFileSync, existsSync, statSync } from "fs";
import { join } from "path";

/**
 * GET /api/hq-audit/last
 *
 * Returns the most recent HQ Audit result from artifacts/hq_audit/<run_id>/.
 * Used by Overview card to show last PASS/FAIL and "Open Summary" link.
 * No secrets; redacted payload only.
 */
export async function GET(req: NextRequest) {
  const origin = req.headers.get("origin");
  const secFetchSite = req.headers.get("sec-fetch-site");
  const tsHost = process.env.OPENCLAW_TAILSCALE_HOSTNAME;
  const allowedOrigin =
    (origin &&
      (origin.includes("127.0.0.1") ||
        origin.includes("localhost") ||
        (tsHost && origin === `https://${tsHost}`))) ||
    secFetchSite === "same-origin";
  if (origin && !allowedOrigin) {
    return NextResponse.json({ ok: false, error: "Forbidden" }, { status: 403 });
  }

  const repoRoot = process.env.OPENCLAW_REPO_ROOT || process.cwd();
  const artifactsRoot = process.env.OPENCLAW_ARTIFACTS_ROOT || join(repoRoot, "artifacts");
  const hqAuditBase = join(artifactsRoot, "hq_audit");
  if (!existsSync(hqAuditBase)) {
    return NextResponse.json({
      ok: true,
      run_id: null,
      overall_pass: null,
      artifact_dir: null,
      summary_path: null,
      tailnet_url: null,
    });
  }

  let dirs: string[];
  try {
    dirs = readdirSync(hqAuditBase).filter((d) => {
      const p = join(hqAuditBase, d);
      return statSync(p).isDirectory();
    });
  } catch {
    return NextResponse.json({
      ok: true,
      run_id: null,
      overall_pass: null,
      artifact_dir: null,
      summary_path: null,
      tailnet_url: null,
    });
  }
  dirs.sort((a, b) => b.localeCompare(a));
  const latestRunId = dirs[0] || null;
  if (!latestRunId) {
    return NextResponse.json({
      ok: true,
      run_id: null,
      overall_pass: null,
      artifact_dir: null,
      summary_path: null,
      tailnet_url: null,
    });
  }

  const summaryPath = join(hqAuditBase, latestRunId, "SUMMARY.json");
  let overallPass: boolean | null = null;
  let tailnetUrl: string | null = null;
  if (existsSync(summaryPath)) {
    try {
      const raw = readFileSync(summaryPath, "utf-8");
      const data = JSON.parse(raw);
      overallPass = data.overall_pass ?? null;
    } catch {
      // ignore parse errors
    }
  }
  const linksPath = join(hqAuditBase, latestRunId, "LINKS.json");
  if (existsSync(linksPath)) {
    try {
      const raw = readFileSync(linksPath, "utf-8");
      const data = JSON.parse(raw);
      tailnetUrl = data.tailnet_url ?? null;
    } catch {
      // ignore
    }
  }

  return NextResponse.json({
    ok: true,
    run_id: latestRunId,
    overall_pass: overallPass,
    artifact_dir: `artifacts/hq_audit/${latestRunId}`,
    summary_path: `artifacts/hq_audit/${latestRunId}/SUMMARY.md`,
    tailnet_url: tailnetUrl,
  });
}
