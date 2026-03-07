import { dirname, join } from "path";
import { existsSync } from "fs";
import { getArtifactsRoot, getRepoRoot, readJsonFile, writeJsonAtomic, ensureDir } from "./server-artifacts";

export type AutonomyMode = "ON" | "OFF";

export interface AutonomyModeState {
  mode: AutonomyMode;
  updated_at: string | null;
  updated_by: string | null;
  path: string;
}

const DEFAULT_SYSTEM_PATH = "/opt/ai-ops-runner/artifacts/system/autonomy_mode.json";

function resolveWritePath(): string {
  if (process.env.OPENCLAW_AUTONOMY_MODE_PATH) {
    return process.env.OPENCLAW_AUTONOMY_MODE_PATH;
  }
  const repoRoot = getRepoRoot();
  if (repoRoot === "/opt/ai-ops-runner" || repoRoot.startsWith("/opt/ai-ops-runner/")) {
    return DEFAULT_SYSTEM_PATH;
  }
  return join(getArtifactsRoot(), "system", "autonomy_mode.json");
}

function resolveReadPath(): string {
  if (process.env.OPENCLAW_AUTONOMY_MODE_PATH) {
    return process.env.OPENCLAW_AUTONOMY_MODE_PATH;
  }
  const candidates = [
    DEFAULT_SYSTEM_PATH,
    join(getArtifactsRoot(), "system", "autonomy_mode.json"),
  ];
  for (const candidate of candidates) {
    if (existsSync(candidate)) return candidate;
  }
  return resolveWritePath();
}

export function readAutonomyMode(): AutonomyModeState {
  const path = resolveReadPath();
  const parsed = readJsonFile<{ mode?: string; updated_at?: string; updated_by?: string }>(path);
  const mode: AutonomyMode = parsed?.mode === "OFF" ? "OFF" : "ON";
  return {
    mode,
    updated_at: typeof parsed?.updated_at === "string" ? parsed.updated_at : null,
    updated_by: typeof parsed?.updated_by === "string" ? parsed.updated_by : null,
    path,
  };
}

export function writeAutonomyMode(mode: AutonomyMode, updatedBy: string): AutonomyModeState {
  const path = resolveWritePath();
  ensureDir(dirname(path));
  const payload = {
    mode,
    updated_at: new Date().toISOString(),
    updated_by: updatedBy,
  };
  writeJsonAtomic(path, payload);
  return {
    ...payload,
    path,
  };
}
