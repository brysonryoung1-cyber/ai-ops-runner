/**
 * GET /api/notifications/banner — Current HQ banner (WAITING_FOR_HUMAN, CANARY_DEGRADED).
 * No auth required. Returns null when no banner.
 */
import { NextResponse } from "next/server";
import { existsSync, readFileSync } from "fs";
import { join } from "path";

export const dynamic = "force-dynamic";

function getBannerPath(): string {
  const artifacts =
    process.env.OPENCLAW_ARTIFACTS_ROOT ||
    join(process.env.OPENCLAW_REPO_ROOT || process.cwd(), "artifacts");
  return join(artifacts, "system", "notification_banner.json");
}

export async function GET() {
  const path = getBannerPath();
  if (!existsSync(path)) {
    return NextResponse.json({ banner: null });
  }
  try {
    const raw = readFileSync(path, "utf-8");
    const banner = JSON.parse(raw);
    return NextResponse.json({ banner });
  } catch {
    return NextResponse.json({ banner: null });
  }
}
