import { NextRequest, NextResponse } from "next/server";
import { readdirSync, readFileSync, existsSync, statSync } from "fs";
import { join } from "path";

/**
 * GET /api/ship/last
 *
 * Returns the most recent ship_result.json from artifacts/ship/<run_id>/.
 * Used by HQ to show last Ship+Deploy result (PASS/FAIL) and artifact links.
 * No secrets; redacted payload only.
 */
export async function GET(req: NextRequest) {
  const origin = req.headers.get("origin");
  const secFetchSite = req.headers.get("sec-fetch-site");
  if (
    origin &&
    !origin.includes("127.0.0.1") &&
    !origin.includes("localhost") &&
    secFetchSite !== "same-origin"
  ) {
    return NextResponse.json({ ok: false, error: "Forbidden" }, { status: 403 });
  }

  const repoRoot = process.env.OPENCLAW_REPO_ROOT || process.cwd();
  const shipBase = join(repoRoot, "artifacts", "ship");
  if (!existsSync(shipBase)) {
    return NextResponse.json({
      ok: true,
      last: null,
      run_id: null,
      overall: null,
      step_failed: null,
      artifact_dir: null,
    });
  }

  let dirs: string[];
  try {
    dirs = readdirSync(shipBase).filter((d) => {
      const p = join(shipBase, d);
      return statSync(p).isDirectory();
    });
  } catch {
    return NextResponse.json({
      ok: true,
      last: null,
      run_id: null,
      overall: null,
      step_failed: null,
      artifact_dir: null,
    });
  }
  dirs.sort((a, b) => b.localeCompare(a));
  const latestRunId = dirs[0] || null;
  if (!latestRunId) {
    return NextResponse.json({
      ok: true,
      last: null,
      run_id: null,
      overall: null,
      step_failed: null,
      artifact_dir: null,
    });
  }

  const resultPath = join(shipBase, latestRunId, "ship_result.json");
  if (!existsSync(resultPath)) {
    return NextResponse.json({
      ok: true,
      last: null,
      run_id: latestRunId,
      overall: null,
      step_failed: null,
      artifact_dir: `artifacts/ship/${latestRunId}`,
    });
  }

  try {
    const raw = readFileSync(resultPath, "utf-8");
    const data = JSON.parse(raw);
    return NextResponse.json({
      ok: true,
      last: data,
      run_id: data.run_id ?? latestRunId,
      overall: data.overall ?? null,
      step_failed: data.step_failed ?? null,
      error_class: data.error_class ?? null,
      next_auto_fix: data.next_auto_fix ?? null,
      artifact_dir: `artifacts/ship/${latestRunId}`,
    });
  } catch {
    return NextResponse.json({
      ok: true,
      last: null,
      run_id: latestRunId,
      overall: null,
      step_failed: null,
      artifact_dir: `artifacts/ship/${latestRunId}`,
    });
  }
}
