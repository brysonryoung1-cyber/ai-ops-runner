import { NextRequest, NextResponse } from "next/server";
import { readFileSync, existsSync } from "fs";
import { join } from "path";

/**
 * GET /api/costs/timeseries?days=30
 * Time series of daily spend. Reads artifacts/cost/usage.jsonl.
 * No secrets. Protected by token auth (middleware).
 */
function getRepoRoot(): string {
  return process.env.OPENCLAW_REPO_ROOT || process.cwd();
}

interface UsageRecord {
  date?: string;
  timestamp_utc?: string;
  cost_usd?: number;
}

function parseUsageJsonl(repoRoot: string): UsageRecord[] {
  const path = join(repoRoot, "artifacts", "cost", "usage.jsonl");
  if (!existsSync(path)) return [];
  const lines = readFileSync(path, "utf-8").split("\n").filter((l) => l.trim());
  const records: UsageRecord[] = [];
  for (const line of lines) {
    try {
      records.push(JSON.parse(line) as UsageRecord);
    } catch {
      // skip
    }
  }
  return records;
}

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
  const days = Math.min(90, Math.max(1, Number(req.nextUrl.searchParams.get("days")) || 30));
  const repoRoot = getRepoRoot();
  const records = parseUsageJsonl(repoRoot);
  const now = new Date();
  const cutoff = new Date(now);
  cutoff.setUTCDate(cutoff.getUTCDate() - days);
  const cutoffStr = cutoff.toISOString().slice(0, 10);
  const byDay: Record<string, number> = {};
  for (const r of records) {
    const date = r.date || (r.timestamp_utc || "").slice(0, 10);
    if (date < cutoffStr) continue;
    byDay[date] = (byDay[date] || 0) + (Number(r.cost_usd) || 0);
  }
  const sortedDays = Object.keys(byDay).sort();
  const series = sortedDays.map((date) => ({
    date,
    usd: Math.round(byDay[date] * 1e4) / 1e4,
  }));
  return NextResponse.json({ ok: true, days, series });
}
