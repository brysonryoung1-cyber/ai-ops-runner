import { NextRequest, NextResponse } from "next/server";
import { readdirSync, existsSync } from "fs";
import { join } from "path";
import { listRunRecords, getRunRecord } from "@/lib/run-recorder";

function getArtifactsRoot(): string {
  if (process.env.OPENCLAW_ARTIFACTS_ROOT) return process.env.OPENCLAW_ARTIFACTS_ROOT;
  const repo = process.env.OPENCLAW_REPO_ROOT || process.cwd();
  return join(repo, "artifacts");
}

/** Resolve hostd artifact dir for a run by matching timestamp. Console run_id = YYYYMMDDHHmmss-XXXX, hostd = YYYYMMDD_HHMMSS_hex. */
function resolveHostdArtifactDirForRun(runId: string): string | null {
  const match = /^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})/.exec(runId);
  if (!match) return null;
  const prefix = `${match[1]}${match[2]}${match[3]}_${match[4]}${match[5]}${match[6]}`;
  const hostdDir = join(getArtifactsRoot(), "hostd");
  if (!existsSync(hostdDir)) return null;
  try {
    const entries = readdirSync(hostdDir, { withFileTypes: true });
    const candidates = entries
      .filter((e) => e.isDirectory() && e.name.startsWith(prefix))
      .map((e) => e.name)
      .sort()
      .reverse();
    if (candidates.length === 0) return null;
    return `artifacts/hostd/${candidates[0]}`;
  } catch {
    return null;
  }
}

/**
 * GET /api/runs
 *
 * Returns recent run records across all projects.
 * Query params:
 *   ?limit=N   — max records to return (default 100, max 500)
 *   ?id=RUN_ID — return a single run record
 *
 * Protected by token auth (middleware).
 * Never leaks secrets.
 */
export async function GET(req: NextRequest) {
  const origin = req.headers.get("origin");
  const secFetchSite = req.headers.get("sec-fetch-site");
  const tsHost = process.env.OPENCLAW_TAILSCALE_HOSTNAME;
  const allowedOrigin =
    (origin && (origin.includes("127.0.0.1") || origin.includes("localhost") || (tsHost && origin === `https://${tsHost}`))) ||
    secFetchSite === "same-origin";
  if (origin && !allowedOrigin) {
    return NextResponse.json({ ok: false, error: "Forbidden" }, { status: 403 });
  }

  const runId = req.nextUrl.searchParams.get("id");

  // Single run lookup
  if (runId) {
    const record = getRunRecord(runId);
    if (!record) {
      return NextResponse.json(
        { ok: false, error: `Run not found: ${runId}` },
        { status: 404 }
      );
    }
    // For apply (and other hostd actions) without artifact_dir, try to resolve from hostd dirs by timestamp
    const runWithArtifacts = { ...record };
    if (!runWithArtifacts.artifact_dir && (record.action === "apply" || record.action === "doctor" || record.action === "guard")) {
      const resolved = resolveHostdArtifactDirForRun(runId);
      if (resolved) runWithArtifacts.artifact_dir = resolved;
    }
    return NextResponse.json({ ok: true, run: runWithArtifacts });
  }

  // List runs
  const limitParam = req.nextUrl.searchParams.get("limit");
  const limit = Math.min(Math.max(1, parseInt(limitParam || "100", 10) || 100), 500);

  const runs = listRunRecords(limit);

  return NextResponse.json({ ok: true, runs, count: runs.length });
}
