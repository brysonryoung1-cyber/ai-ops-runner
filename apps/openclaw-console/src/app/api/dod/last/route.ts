import { NextRequest, NextResponse } from "next/server";
import { readdirSync, readFileSync, existsSync, statSync } from "fs";
import { join } from "path";

/**
 * GET /api/dod/last
 *
 * Returns the most recent dod_result.json from artifacts/dod/<run_id>/.
 * Used by HQ to show last Definition-of-Done result (PASS/FAIL) and artifact path.
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
  const dodBase = join(repoRoot, "artifacts", "dod");
  if (!existsSync(dodBase)) {
    return NextResponse.json({
      ok: true,
      last: null,
      run_id: null,
      overall: null,
      artifact_dir: null,
    });
  }

  let dirs: string[];
  try {
    dirs = readdirSync(dodBase).filter((d) => {
      const p = join(dodBase, d);
      return statSync(p).isDirectory();
    });
  } catch {
    return NextResponse.json({
      ok: true,
      last: null,
      run_id: null,
      overall: null,
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
      artifact_dir: null,
    });
  }

  const resultPath = join(dodBase, latestRunId, "dod_result.json");
  if (!existsSync(resultPath)) {
    return NextResponse.json({
      ok: true,
      last: null,
      run_id: latestRunId,
      overall: null,
      artifact_dir: `artifacts/dod/${latestRunId}`,
    });
  }

  try {
    const raw = readFileSync(resultPath, "utf-8");
    const data = JSON.parse(raw);
    return NextResponse.json({
      ok: true,
      last: data,
      run_id: data.run_id ?? latestRunId,
      overall: data.ok === true ? "PASS" : "FAIL",
      artifact_dir: data.artifact_dir ?? `artifacts/dod/${latestRunId}`,
    });
  } catch {
    return NextResponse.json({
      ok: true,
      last: null,
      run_id: latestRunId,
      overall: null,
      artifact_dir: `artifacts/dod/${latestRunId}`,
    });
  }
}
