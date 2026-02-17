import { NextRequest, NextResponse } from "next/server";
import { readFileSync, existsSync } from "fs";
import { join } from "path";

/**
 * GET /api/costs/summary
 * Today spend, MTD, by project, top actions/models. Reads artifacts/cost/usage.jsonl.
 * No secrets. Protected by token auth (middleware).
 */
function getRepoRoot(): string {
  return process.env.OPENCLAW_REPO_ROOT || process.cwd();
}

interface UsageRecord {
  timestamp_utc?: string;
  date?: string;
  hour?: string;
  project_id?: string;
  action?: string;
  model?: string;
  provider?: string;
  prompt_tokens?: number;
  completion_tokens?: number;
  total_tokens?: number;
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
      // skip malformed
    }
  }
  return records;
}

function buildSummary(records: UsageRecord[]): Record<string, unknown> {
  const now = new Date();
  const today = now.toISOString().slice(0, 10);
  const monthStart = `${now.getUTCFullYear()}-${String(now.getUTCMonth() + 1).padStart(2, "0")}-01`;
  const byDay: Record<string, number> = {};
  const byProject: Record<string, number> = {};
  const byAction: Record<string, number> = {};
  const byModel: Record<string, number> = {};
  let todayUsd = 0;
  let mtdUsd = 0;
  let last7Usd = 0;
  const sevenDaysAgo = new Date(now);
  sevenDaysAgo.setUTCDate(sevenDaysAgo.getUTCDate() - 7);
  const cutoff7 = sevenDaysAgo.toISOString().slice(0, 10);

  for (const r of records) {
    const date = r.date || (r.timestamp_utc || "").slice(0, 10);
    const cost = Number(r.cost_usd) || 0;
    byDay[date] = (byDay[date] || 0) + cost;
    byProject[r.project_id || "default"] = (byProject[r.project_id || "default"] || 0) + cost;
    byAction[r.action || "unknown"] = (byAction[r.action || "unknown"] || 0) + cost;
    byModel[r.model || "unknown"] = (byModel[r.model || "unknown"] || 0) + cost;
    if (date === today) todayUsd += cost;
    if (date >= monthStart) mtdUsd += cost;
    if (date >= cutoff7) last7Usd += cost;
  }

  const topProject = Object.entries(byProject).sort((a, b) => b[1] - a[1])[0] || ["", 0];
  return {
    ok: true,
    today_usd: Math.round(todayUsd * 1e4) / 1e4,
    mtd_usd: Math.round(mtdUsd * 1e4) / 1e4,
    last_7_days_usd: Math.round(last7Usd * 1e4) / 1e4,
    top_project: { id: topProject[0], usd: Math.round(topProject[1] * 1e4) / 1e4 },
    by_project: Object.fromEntries(
      Object.entries(byProject)
        .sort((a, b) => b[1] - a[1])
        .slice(0, 10)
        .map(([k, v]) => [k, Math.round(v * 1e4) / 1e4])
    ),
    by_action: Object.fromEntries(
      Object.entries(byAction)
        .sort((a, b) => b[1] - a[1])
        .slice(0, 10)
        .map(([k, v]) => [k, Math.round(v * 1e4) / 1e4])
    ),
    by_model: Object.fromEntries(
      Object.entries(byModel)
        .sort((a, b) => b[1] - a[1])
        .slice(0, 10)
        .map(([k, v]) => [k, Math.round(v * 1e4) / 1e4])
    ),
    last_poll_time: null as string | null,
  };
}

function loadGuardLimits(repoRoot: string): { hourly_usd_limit: number; daily_usd_limit: number } {
  const defaults = { hourly_usd_limit: 20, daily_usd_limit: 100 };
  for (const name of ["cost_guard.json", "llm.json"]) {
    const path = join(repoRoot, "config", name);
    if (!existsSync(path)) continue;
    try {
      const data = JSON.parse(readFileSync(path, "utf-8"));
      if (name === "cost_guard.json") {
        return {
          hourly_usd_limit: Number(data.hourly_usd_limit) || defaults.hourly_usd_limit,
          daily_usd_limit: Number(data.daily_usd_limit) || defaults.daily_usd_limit,
        };
      }
      const budget = data.budget || data;
      return {
        hourly_usd_limit: Number(budget.hourly_usd_limit) || defaults.hourly_usd_limit,
        daily_usd_limit: Number(budget.daily_usd_limit) || defaults.daily_usd_limit,
      };
    } catch {
      continue;
    }
  }
  return defaults;
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
  const repoRoot = getRepoRoot();
  const records = parseUsageJsonl(repoRoot);
  const summary = buildSummary(records) as Record<string, unknown>;
  const now = new Date();
  const today = now.toISOString().slice(0, 10);
  const hourKey = now.toISOString().slice(0, 13);
  const todayUsd = records
    .filter((r) => (r.date || (r.timestamp_utc || "").slice(0, 10)) === today)
    .reduce((s, r) => s + (Number(r.cost_usd) || 0), 0);
  const lastHourUsd = records
    .filter((r) => (r.hour || (r.timestamp_utc || "").slice(0, 13)) === hourKey)
    .reduce((s, r) => s + (Number(r.cost_usd) || 0), 0);
  const { hourly_usd_limit, daily_usd_limit } = loadGuardLimits(repoRoot);
  summary.guard_tripped = lastHourUsd >= hourly_usd_limit || todayUsd >= daily_usd_limit;
  summary.hourly_limit_usd = hourly_usd_limit;
  summary.daily_limit_usd = daily_usd_limit;
  return NextResponse.json(summary);
}
