import { NextRequest, NextResponse } from "next/server";
import { readFileSync, existsSync } from "fs";
import { join } from "path";

const ALLOWED_DOCS = new Set([
  "OPENCLAW_GOALS",
  "OPENCLAW_ROADMAP",
  "OPENCLAW_DECISIONS",
  "OPENCLAW_CURRENT",
  "OPENCLAW_NEXT",
]);

/**
 * GET /api/project/docs/:name
 * Returns markdown content for canonical doc (Goals, Roadmap, Decisions, Current, Next).
 * No secrets. Protected by token auth.
 */
export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ name: string }> }
) {
  const { name } = await params;
  if (!name || !ALLOWED_DOCS.has(name)) {
    return NextResponse.json(
      { ok: false, error: "Unknown doc name" },
      { status: 400 }
    );
  }
  const repoRoot = process.env.OPENCLAW_REPO_ROOT || process.cwd();
  const path = join(repoRoot, "docs", `${name}.md`);
  if (!existsSync(path)) {
    return NextResponse.json(
      { ok: false, error: "Doc not found" },
      { status: 404 }
    );
  }
  try {
    const content = readFileSync(path, "utf-8");
    return NextResponse.json({ ok: true, name, content });
  } catch (e: any) {
    return NextResponse.json(
      { ok: false, error: e?.message || "Read failed" },
      { status: 500 }
    );
  }
}
