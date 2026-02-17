/**
 * Unified Run Recorder for OpenClaw HQ.
 *
 * Every action (doctor, apply, review, any workflow) writes a run record
 * to artifacts/runs/<run_id>/run.json. Records are written even on failure
 * (fail-closed: if the recorder itself fails, the error is logged but
 * the action result is still returned to the caller).
 *
 * Security: No secrets are ever included in run records.
 */

import { writeFileSync, mkdirSync, readFileSync, readdirSync, statSync } from "fs";
import { join } from "path";

// ── Types ──────────────────────────────────────────────────────

export interface RunRecord {
  run_id: string;
  project_id: string;
  action: string;
  started_at: string;
  finished_at: string;
  status: "success" | "failure" | "error";
  exit_code: number | null;
  duration_ms: number;
  error_summary: string | null;
  artifact_paths: string[];
}

// ── Helpers ────────────────────────────────────────────────────

/**
 * Generate a unique run ID: timestamp + random suffix.
 * Format: YYYYMMDD-HHmmss-XXXX
 */
export function generateRunId(): string {
  const now = new Date();
  const ts = now.toISOString().replace(/[-:T]/g, "").slice(0, 14);
  const rand = Math.random().toString(36).slice(2, 6);
  return `${ts}-${rand}`;
}

/**
 * Map action names to project IDs.
 * Actions belonging to a known project are attributed to it;
 * unknown actions default to "infra_openclaw".
 */
const ACTION_PROJECT_MAP: Record<string, string> = {
  // infra_openclaw
  doctor: "infra_openclaw",
  llm_doctor: "infra_openclaw",
  apply: "infra_openclaw",
  guard: "infra_openclaw",
  ports: "infra_openclaw",
  timer: "infra_openclaw",
  journal: "infra_openclaw",
  artifacts: "infra_openclaw",
  deploy_and_verify: "infra_openclaw",
  // soma_kajabi_library_ownership
  soma_snapshot_home: "soma_kajabi_library_ownership",
  soma_snapshot_practitioner: "soma_kajabi_library_ownership",
  soma_harvest: "soma_kajabi_library_ownership",
  soma_mirror: "soma_kajabi_library_ownership",
  soma_kajabi_phase0: "soma_kajabi",
  soma_status: "soma_kajabi_library_ownership",
  soma_last_errors: "soma_kajabi_library_ownership",
  sms_status: "soma_kajabi_library_ownership",
};

export function resolveProjectId(action: string): string {
  return ACTION_PROJECT_MAP[action] || "infra_openclaw";
}

// ── Resolve runs directory ─────────────────────────────────────

function resolveRunsDir(): string {
  const candidates = [
    join(process.cwd(), "artifacts", "runs"),
    join(process.cwd(), "..", "..", "artifacts", "runs"),
  ];

  // Use whichever parent artifacts/ dir exists, or default to first
  for (const candidate of candidates) {
    try {
      const parent = join(candidate, "..");
      statSync(parent);
      return candidate;
    } catch {
      // Try next
    }
  }
  return candidates[0];
}

// ── Write ──────────────────────────────────────────────────────

/**
 * Write a run record to artifacts/runs/<run_id>/run.json.
 *
 * Creates the directory structure if it doesn't exist.
 * Returns the run_id on success, or null on failure (never throws).
 */
export function writeRunRecord(record: RunRecord): string | null {
  try {
    const runsDir = resolveRunsDir();
    const runDir = join(runsDir, record.run_id);
    mkdirSync(runDir, { recursive: true });
    const runPath = join(runDir, "run.json");
    writeFileSync(runPath, JSON.stringify(record, null, 2) + "\n", "utf-8");
    return record.run_id;
  } catch (err) {
    console.error(
      `[RunRecorder] Failed to write run record ${record.run_id}: ${
        err instanceof Error ? err.message : String(err)
      }`
    );
    return null;
  }
}

/**
 * Build a RunRecord from action execution results.
 * @param runId — optional; when provided (e.g. from action lock) use for single-flight join semantics.
 */
export function buildRunRecord(
  action: string,
  startedAt: Date,
  finishedAt: Date,
  exitCode: number | null,
  ok: boolean,
  errorMsg: string | null,
  runId?: string
): RunRecord {
  return {
    run_id: runId ?? generateRunId(),
    project_id: resolveProjectId(action),
    action,
    started_at: startedAt.toISOString(),
    finished_at: finishedAt.toISOString(),
    status: ok ? "success" : exitCode !== null ? "failure" : "error",
    exit_code: exitCode,
    duration_ms: finishedAt.getTime() - startedAt.getTime(),
    error_summary: errorMsg,
    artifact_paths: [],
  };
}

// ── Read ───────────────────────────────────────────────────────

/**
 * List all run records, most recent first.
 * Returns up to `limit` records (default 100).
 * Never throws; returns empty array on failure.
 */
export function listRunRecords(limit = 100): RunRecord[] {
  try {
    const runsDir = resolveRunsDir();
    let dirs: string[];
    try {
      dirs = readdirSync(runsDir);
    } catch {
      return []; // No runs dir yet
    }

    // Sort by directory name descending (newest first since names start with timestamp)
    dirs.sort((a, b) => b.localeCompare(a));
    dirs = dirs.slice(0, limit);

    const records: RunRecord[] = [];
    for (const dir of dirs) {
      try {
        const runPath = join(runsDir, dir, "run.json");
        const raw = readFileSync(runPath, "utf-8");
        records.push(JSON.parse(raw));
      } catch {
        // Skip malformed entries
      }
    }

    return records;
  } catch {
    return [];
  }
}

/**
 * Get a single run record by ID.
 * Returns null if not found.
 */
export function getRunRecord(runId: string): RunRecord | null {
  try {
    // Sanitize: only allow alphanumeric + dash
    if (!/^[a-zA-Z0-9-]+$/.test(runId)) {
      return null;
    }
    const runsDir = resolveRunsDir();
    const runPath = join(runsDir, runId, "run.json");
    const raw = readFileSync(runPath, "utf-8");
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

/**
 * Get the most recent run for a specific project.
 * Returns null if no runs found.
 */
export function getLastRunForProject(projectId: string): RunRecord | null {
  const runs = listRunRecords(500);
  return runs.find((r) => r.project_id === projectId) || null;
}

/**
 * Get all runs for a specific project (most recent first, up to limit).
 */
export function getRunsForProject(projectId: string, limit = 50): RunRecord[] {
  const runs = listRunRecords(500);
  return runs.filter((r) => r.project_id === projectId).slice(0, limit);
}
