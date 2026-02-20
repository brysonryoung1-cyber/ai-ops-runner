import { NextRequest, NextResponse } from "next/server";
import { readdirSync, statSync, readFileSync, existsSync } from "fs";
import { join, extname, resolve, relative } from "path";

function getArtifactsRoot(): string {
  if (process.env.OPENCLAW_ARTIFACTS_ROOT) return process.env.OPENCLAW_ARTIFACTS_ROOT;
  const repo = process.env.OPENCLAW_REPO_ROOT || process.cwd();
  return join(repo, "artifacts");
}

const MAX_ENTRIES = 200;
const MAX_FILE_SIZE = 512 * 1024; // 512KB max for inline viewing

/**
 * Resolve path under root; reject path traversal. Returns null if invalid.
 * Uses path.relative to ensure the resolved path is strictly within root.
 */
function safeJoin(root: string, ...segments: string[]): string | null {
  const absRoot = resolve(root);
  const resolved = resolve(root, ...segments);
  const rel = relative(absRoot, resolved);
  if (rel.startsWith("..") || rel.startsWith("/")) return null;
  if (!resolved.startsWith(absRoot + "/") && resolved !== absRoot) return null;
  return resolved;
}

/** Stub data when OPENCLAW_UI_STUB=1 */
function stubResponse(pathStr: string): NextResponse {
  const segments = pathStr.split("/").filter(Boolean);
  if (segments.length === 0) {
    return NextResponse.json({
      entries: [
        { name: "runs", type: "dir" },
        { name: "ui_telemetry", type: "dir" },
        { name: "hostd", type: "dir" },
      ],
    });
  }
  if (segments[0] === "runs") {
    if (segments.length === 1) {
      return NextResponse.json({
        entries: [
          { name: "20260217-120000-ab12", type: "dir" },
          { name: "20260216-100000-cd34", type: "dir" },
        ],
      });
    }
    return NextResponse.json({
      entries: [
        { name: "run.json", type: "file", size: 512 },
        { name: "SUMMARY.md", type: "file", size: 128 },
      ],
    });
  }
  if (segments[segments.length - 1].includes(".")) {
    const name = segments[segments.length - 1];
    const ext = extname(name).toLowerCase();
    if (ext === ".json") {
      return NextResponse.json({
        content: JSON.stringify({ stub: true, path: pathStr }, null, 2),
        contentType: "json",
        fileName: name,
        entries: [],
      });
    }
    if (ext === ".md") {
      return NextResponse.json({
        content: `# Stub artifact\n\nPath: ${pathStr}\n`,
        contentType: "markdown",
        fileName: name,
        entries: [],
      });
    }
    return NextResponse.json({
      content: `Stub content for ${pathStr}\n`,
      contentType: "text",
      fileName: name,
      entries: [],
    });
  }
  return NextResponse.json({
    entries: [
      { name: "SUMMARY.md", type: "file", size: 256 },
      { name: "event.json", type: "file", size: 384 },
    ],
  });
}

/**
 * Determine content type from file extension.
 */
function classifyFile(name: string): "markdown" | "json" | "text" | "binary" {
  const ext = extname(name).toLowerCase();
  if (ext === ".md") return "markdown";
  if (ext === ".json") return "json";
  if ([".txt", ".log", ".csv", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".sh", ".py", ".ts", ".js"].includes(ext)) return "text";
  return "binary";
}

function getAllowedOrigins(): Set<string> {
  const port = process.env.OPENCLAW_CONSOLE_PORT || process.env.PORT || "8787";
  const origins = new Set([
    `http://127.0.0.1:${port}`,
    "http://127.0.0.1:3000",
    "http://127.0.0.1:8787",
    "http://localhost:3000",
    `http://localhost:${port}`,
  ]);
  const tsHostname = process.env.OPENCLAW_TAILSCALE_HOSTNAME;
  if (tsHostname) origins.add(`https://${tsHostname}`);
  return origins;
}

/**
 * GET /api/artifacts/browse?path=<encoded-path>[&download=1]
 *
 * Browse artifact subdirectories and view files.
 * Path traversal prevented. No secrets; only directory names, file names, and safe file contents.
 */
export async function GET(req: NextRequest) {
  const allowedOrigins = getAllowedOrigins();
  const origin = req.headers.get("origin");
  const secFetchSite = req.headers.get("sec-fetch-site");
  if (origin && !allowedOrigins.has(origin) && secFetchSite !== "same-origin") {
    return NextResponse.json({ ok: false, error: "Forbidden" }, { status: 403 });
  }
  const host = req.headers.get("host") ?? "";
  if (!origin && secFetchSite !== "same-origin") {
    const tsHost = process.env.OPENCLAW_TAILSCALE_HOSTNAME;
    const allowedHost =
      host.startsWith("127.0.0.1:") || host.startsWith("localhost:") || (tsHost && host === tsHost);
    if (!allowedHost) {
      return NextResponse.json({ ok: false, error: "Forbidden" }, { status: 403 });
    }
  }

  const pathParam = req.nextUrl.searchParams.get("path") || "";
  const download = req.nextUrl.searchParams.get("download") === "1";

  // Stub mode for testing
  if (process.env.OPENCLAW_UI_STUB === "1") {
    return stubResponse(pathParam);
  }

  const ARTIFACTS_ROOT = getArtifactsRoot();
  if (!existsSync(ARTIFACTS_ROOT)) {
    return NextResponse.json({ ok: false, error: "Artifacts root not found" }, { status: 503 });
  }

  // Decode path segments and validate
  const segments = pathParam
    .split("/")
    .filter(Boolean)
    .map(decodeURIComponent);

  for (const seg of segments) {
    if (seg === ".." || seg === "." || seg.includes("/") || seg.includes("\\")) {
      return NextResponse.json({ ok: false, error: "Invalid path" }, { status: 400 });
    }
  }

  const fullPath = safeJoin(ARTIFACTS_ROOT, ...segments);
  if (!fullPath) {
    return NextResponse.json({ ok: false, error: "Invalid path" }, { status: 400 });
  }

  if (!existsSync(fullPath)) {
    return NextResponse.json({ ok: false, error: "Not found" }, { status: 404 });
  }

  try {
    const stat = statSync(fullPath);

    if (stat.isDirectory()) {
      const rawEntries = readdirSync(fullPath, { withFileTypes: true });
      const entries: { name: string; type: "dir" | "file"; size?: number }[] = [];
      let count = 0;
      for (const e of rawEntries) {
        if (count >= MAX_ENTRIES) break;
        if (e.name.startsWith(".")) continue;
        if (e.name.includes("..")) continue;
        const entryPath = safeJoin(fullPath, e.name);
        if (!entryPath) continue;
        try {
          const entryStat = statSync(entryPath);
          entries.push({
            name: e.name,
            type: entryStat.isDirectory() ? "dir" : "file",
            size: entryStat.isFile() ? entryStat.size : undefined,
          });
        } catch {
          continue;
        }
        count++;
      }
      entries.sort((a, b) => {
        if (a.type !== b.type) return a.type === "dir" ? -1 : 1;
        return a.name.localeCompare(b.name);
      });
      return NextResponse.json({ entries });
    }

    if (stat.isFile()) {
      const fileName = segments[segments.length - 1] || "file";

      if (download) {
        const content = readFileSync(fullPath);
        const sanitizedName = fileName.replace(/[^\w._-]/g, "_");
        return new NextResponse(content, {
          headers: {
            "Content-Type": "application/octet-stream",
            "Content-Disposition": `attachment; filename="${sanitizedName}"`,
          },
        });
      }

      const contentType = classifyFile(fileName);

      if (contentType === "binary" || stat.size > MAX_FILE_SIZE) {
        return NextResponse.json({
          content: null,
          contentType: "binary",
          fileName,
          entries: [],
        });
      }

      const raw = readFileSync(fullPath, "utf-8");
      const content = contentType === "json"
        ? (() => { try { return JSON.stringify(JSON.parse(raw), null, 2); } catch { return raw; } })()
        : raw;

      return NextResponse.json({
        content,
        contentType,
        fileName,
        entries: [],
      });
    }

    return NextResponse.json({ ok: false, error: "Unsupported entry type" }, { status: 400 });
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return NextResponse.json({ ok: false, error: `Browse failed: ${message}` }, { status: 500 });
  }
}
