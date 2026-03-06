/**
 * GET /api/notifications/banner — Current HQ banner (WAITING_FOR_HUMAN, CANARY_DEGRADED, CANARY_WARN).
 * No auth required. Returns null when no banner.
 */
import { NextResponse } from "next/server";
import { existsSync, readFileSync, readdirSync } from "fs";
import { join } from "path";

export const dynamic = "force-dynamic";

type BannerPayload = Record<string, unknown> & {
  type?: string;
  created_at?: string;
};

function getBannerPath(): string {
  const artifacts =
    process.env.OPENCLAW_ARTIFACTS_ROOT ||
    join(process.env.OPENCLAW_REPO_ROOT || process.cwd(), "artifacts");
  return join(artifacts, "system", "notification_banner.json");
}

function getArtifactsRoot(): string {
  return (
    process.env.OPENCLAW_ARTIFACTS_ROOT ||
    join(process.env.OPENCLAW_REPO_ROOT || process.cwd(), "artifacts")
  );
}

function readBannerFile(): BannerPayload | null {
  const path = getBannerPath();
  if (!existsSync(path)) return null;
  try {
    const raw = readFileSync(path, "utf-8");
    const parsed = JSON.parse(raw);
    return typeof parsed === "object" && parsed ? (parsed as BannerPayload) : null;
  } catch {
    return null;
  }
}

function latestCanaryResult():
  | { result: Record<string, unknown>; runId: string }
  | null {
  const base = join(getArtifactsRoot(), "system", "canary");
  if (!existsSync(base)) return null;
  const runDirs = readdirSync(base)
    .filter((entry) => existsSync(join(base, entry, "result.json")))
    .sort((a, b) => b.localeCompare(a));
  if (runDirs.length === 0) return null;
  const runId = runDirs[0];
  const resultPath = join(base, runId, "result.json");
  try {
    const raw = readFileSync(resultPath, "utf-8");
    const parsed = JSON.parse(raw);
    if (typeof parsed !== "object" || !parsed) return null;
    return { result: parsed as Record<string, unknown>, runId };
  } catch {
    return null;
  }
}

function toStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => String(item || "").trim())
    .filter((item) => item.length > 0);
}

function deriveCanaryBanner():
  | (BannerPayload & { type: "CANARY_DEGRADED" | "CANARY_WARN" })
  | null {
  const latest = latestCanaryResult();
  if (!latest) return null;

  const coreStatus = String(latest.result.core_status || "").toUpperCase();
  const optionalStatus = String(latest.result.optional_status || "").toUpperCase();
  const coreFailed = toStringArray(latest.result.core_failed_checks);
  const optionalFailed = toStringArray(latest.result.optional_failed_checks);
  const proof = String(latest.result.proof || "").trim();
  const createdAt = new Date().toISOString();

  if (coreStatus === "FAIL" || coreFailed.length > 0) {
    const fallback = String(latest.result.failed_invariant || "").trim();
    const failedChecks = coreFailed.length > 0 ? coreFailed : (fallback ? [fallback] : []);
    return {
      type: "CANARY_DEGRADED",
      created_at: createdAt,
      run_id: latest.runId,
      failed_invariant: failedChecks[0] || null,
      failed_checks: failedChecks,
      proof_paths: proof ? [proof] : [],
      severity: "CORE",
    };
  }

  if (optionalStatus === "WARN" || optionalFailed.length > 0) {
    return {
      type: "CANARY_WARN",
      created_at: createdAt,
      run_id: latest.runId,
      warning_checks: optionalFailed,
      proof_paths: proof ? [proof] : [],
      severity: "OPTIONAL",
      message: "Canary optional checks degraded",
    };
  }

  return null;
}

export async function GET() {
  const persistedBanner = readBannerFile();
  if (persistedBanner?.type === "WAITING_FOR_HUMAN") {
    return NextResponse.json({ banner: persistedBanner });
  }

  const canaryBanner = deriveCanaryBanner();
  if (canaryBanner) {
    return NextResponse.json({ banner: canaryBanner });
  }

  if (
    persistedBanner &&
    persistedBanner.type !== "CANARY_DEGRADED" &&
    persistedBanner.type !== "CANARY_WARN"
  ) {
    return NextResponse.json({ banner: persistedBanner });
  }

  return NextResponse.json({ banner: null });
}
