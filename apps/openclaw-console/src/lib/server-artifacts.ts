import { randomBytes } from "crypto";
import { existsSync, mkdirSync, readFileSync, readdirSync, renameSync, writeFileSync } from "fs";
import { join, dirname } from "path";

export function getRepoRoot(): string {
  return process.env.OPENCLAW_REPO_ROOT || process.cwd();
}

export function getArtifactsRoot(): string {
  return process.env.OPENCLAW_ARTIFACTS_ROOT || join(getRepoRoot(), "artifacts");
}

export function ensureDir(path: string): void {
  mkdirSync(path, { recursive: true });
}

export function generateStampedId(prefix: string): string {
  const ts = new Date().toISOString().replace(/[-:]/g, "").replace(/\.\d+Z$/, "Z").replace("T", "T");
  return `${prefix}_${ts}_${randomBytes(3).toString("hex")}`;
}

export function readJsonFile<T>(path: string): T | null {
  try {
    if (!existsSync(path)) return null;
    return JSON.parse(readFileSync(path, "utf-8")) as T;
  } catch {
    return null;
  }
}

export function writeTextAtomic(path: string, content: string): void {
  ensureDir(dirname(path));
  const tmpPath = `${path}.tmp-${randomBytes(2).toString("hex")}`;
  writeFileSync(tmpPath, content, "utf-8");
  renameSync(tmpPath, path);
}

export function writeJsonAtomic(path: string, payload: unknown): void {
  writeTextAtomic(path, JSON.stringify(payload, null, 2) + "\n");
}

export function listChildDirectories(path: string): string[] {
  try {
    if (!existsSync(path)) return [];
    return readdirSync(path, { withFileTypes: true })
      .filter((entry) => entry.isDirectory())
      .map((entry) => entry.name)
      .sort((a, b) => b.localeCompare(a));
  } catch {
    return [];
  }
}

export function toArtifactRelativePath(pathValue: string | null | undefined): string | null {
  if (!pathValue) return null;
  const normalized = String(pathValue).replace(/\\/g, "/");
  const artifactsRoot = getArtifactsRoot().replace(/\\/g, "/").replace(/\/$/, "");
  const repoRoot = getRepoRoot().replace(/\\/g, "/").replace(/\/$/, "");
  if (normalized.startsWith("artifacts/")) return normalized.replace(/^artifacts\/+/, "artifacts/");
  if (normalized.startsWith(`${artifactsRoot}/`)) {
    return `artifacts/${normalized.slice(artifactsRoot.length + 1)}`;
  }
  if (normalized.startsWith(`${repoRoot}/artifacts/`)) {
    return `artifacts/${normalized.slice(repoRoot.length + "/artifacts/".length + 1)}`;
  }
  return normalized;
}

export function toArtifactUrl(pathValue: string | null | undefined): string | null {
  const rel = toArtifactRelativePath(pathValue);
  if (!rel) return null;
  const clean = rel.replace(/^artifacts\/?/, "");
  if (!clean) return null;
  return `/artifacts/${clean.split("/").map(encodeURIComponent).join("/")}`;
}
