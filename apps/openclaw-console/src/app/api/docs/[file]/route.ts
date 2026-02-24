/**
 * GET /api/docs/[file] — Serve docs from repo/docs/ (read-only, no secrets).
 * Used for SOMA_LOCKED_SPEC.md, SOMA_ACCEPTANCE_CHECKLIST.md.
 */
import { NextRequest, NextResponse } from "next/server";
import { join, resolve, relative } from "path";
import { readFileSync, existsSync } from "fs";

const ALLOWED_DOCS = new Set(["SOMA_LOCKED_SPEC.md", "SOMA_ACCEPTANCE_CHECKLIST.md"]);

function getDocsRoot(): string {
  const repo = process.env.OPENCLAW_REPO_ROOT || process.cwd();
  return resolve(join(repo, "docs"));
}

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ file: string }> }
) {
  const { file } = await params;
  if (!file || !ALLOWED_DOCS.has(file)) {
    return NextResponse.json({ error: "Not found" }, { status: 404 });
  }
  const docsRoot = getDocsRoot();
  const fullPath = resolve(join(docsRoot, file));
  const rel = relative(docsRoot, fullPath);
  if (rel.startsWith("..") || rel.includes("..") || !existsSync(fullPath)) {
    return NextResponse.json({ error: "Not found" }, { status: 404 });
  }
  try {
    const content = readFileSync(fullPath, "utf-8");
    return new NextResponse(content, {
      headers: { "Content-Type": "text/markdown; charset=utf-8" },
    });
  } catch {
    return NextResponse.json({ error: "Read failed" }, { status: 500 });
  }
}
