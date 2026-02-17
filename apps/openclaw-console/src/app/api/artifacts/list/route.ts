import { NextRequest, NextResponse } from "next/server";
import { readdirSync, statSync, existsSync } from "fs";
import { join } from "path";

function getArtifactsRoot(): string {
  if (process.env.OPENCLAW_ARTIFACTS_ROOT) return process.env.OPENCLAW_ARTIFACTS_ROOT;
  const repo = process.env.OPENCLAW_REPO_ROOT || process.cwd();
  return join(repo, "artifacts");
}
const MAX_ENTRIES = 50;

/**
 * Resolve path under root; reject path traversal. Returns null if invalid.
 */
function safeJoin(root: string, ...segments: string[]): string | null {
  const resolved = join(root, ...segments);
  const normalized = join(resolved);
  if (!normalized.startsWith(join(root))) return null;
  return normalized;
}

/**
 * GET /api/artifacts/list
 * List top-level directories under OPENCLAW_ARTIFACTS_ROOT (read-only mount).
 * Path traversal prevented. No secrets; only directory names and sizes.
 */
const ALLOWED_ORIGINS = new Set([
  "http://127.0.0.1:3000",
  "http://127.0.0.1:8787",
  "http://localhost:3000",
  "http://localhost:8787",
]);

export async function GET(req: NextRequest) {
  const origin = req.headers.get("origin");
  const secFetchSite = req.headers.get("sec-fetch-site");
  if (origin && !ALLOWED_ORIGINS.has(origin) && secFetchSite !== "same-origin") {
    return NextResponse.json({ ok: false, error: "Forbidden" }, { status: 403 });
  }
  const host = req.headers.get("host") ?? "";
  if (!origin && secFetchSite !== "same-origin") {
    const allowedHost = host === "127.0.0.1:3000" || host === "127.0.0.1:8787" || host === "localhost:3000" || host === "localhost:8787";
    if (!allowedHost) {
      return NextResponse.json({ ok: false, error: "Forbidden" }, { status: 403 });
    }
  }

  const ARTIFACTS_ROOT = getArtifactsRoot();
  if (!existsSync(ARTIFACTS_ROOT)) {
    return NextResponse.json(
      { ok: false, error: "Artifacts root not found" },
      { status: 503 }
    );
  }
  try {
    const entries = readdirSync(ARTIFACTS_ROOT, { withFileTypes: true });
    const dirs: { name: string; size?: string }[] = [];
    let count = 0;
    for (const e of entries) {
      if (count >= MAX_ENTRIES) break;
      if (!e.isDirectory()) continue;
      if (e.name.includes("..") || e.name.includes("/") || e.name.includes("\\")) continue;
      const fullPath = safeJoin(ARTIFACTS_ROOT, e.name);
      if (!fullPath) continue; // path traversal
      try {
        const stat = statSync(fullPath);
        if (!stat.isDirectory()) continue;
      } catch {
        continue;
      }
      dirs.push({ name: e.name });
      count++;
    }
    // Sort by name descending (often run_id-like) for recency
    dirs.sort((a, b) => b.name.localeCompare(a.name));
    return NextResponse.json({ ok: true, dirs });
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return NextResponse.json(
      { ok: false, error: `Artifacts root not available: ${message}` },
      { status: 503 }
    );
  }
}
