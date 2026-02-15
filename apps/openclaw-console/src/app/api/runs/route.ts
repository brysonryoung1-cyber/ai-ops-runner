import { NextRequest, NextResponse } from "next/server";
import { listRunRecords, getRunRecord } from "@/lib/run-recorder";

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
  if (origin && !origin.includes("127.0.0.1") && !origin.includes("localhost") && secFetchSite !== "same-origin") {
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
    return NextResponse.json({ ok: true, run: record });
  }

  // List runs
  const limitParam = req.nextUrl.searchParams.get("limit");
  const limit = Math.min(Math.max(1, parseInt(limitParam || "100", 10) || 100), 500);

  const runs = listRunRecords(limit);

  return NextResponse.json({ ok: true, runs, count: runs.length });
}
