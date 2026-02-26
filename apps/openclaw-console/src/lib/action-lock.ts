/**
 * Action Lock — prevents overlapping execution of the same action.
 * Single-flight + join: 409 responses ALWAYS include active_run_id so callers can poll /api/runs?id=<run_id>.
 *
 * Uses in-memory map + file persistence (artifacts/.locks/<action>.json) for:
 *   - Multi-instance / restart resilience
 *   - Stale-lock auto-clear (TTL 30 min for long-running; 5 min for normal)
 *
 * Lock file shape: { run_id, started_at, last_heartbeat_at, state }
 * TTL: if last_heartbeat_at (or started_at) older than STALE_MS, treat as stale and clear.
 */

import { randomBytes } from "crypto";
import { existsSync, readFileSync, writeFileSync, unlinkSync, mkdirSync } from "fs";
import { join } from "path";

export interface LockAcquireResult {
  acquired: boolean;
  /** Set when acquired; use for buildRunRecord. */
  runId?: string;
  /** Set when not acquired; use for 409 response (joinable). ALWAYS present when acquired=false. */
  existing?: { runId: string; startedAt: number };
}

interface LockEntry {
  acquiredAt: number;
  runId: string;
}

interface LockFileShape {
  run_id: string;
  started_at: string;
  last_heartbeat_at: string;
  state?: string;
  /** Set by soma_kajabi_auto_finish so HQ can find run artifacts even if process dies. */
  artifact_dir?: string;
}

const locks = new Map<string, LockEntry>();

/** Actions that are safe to run concurrently (read-only) */
const CONCURRENT_ALLOWED: ReadonlySet<string> = new Set([
  "ports",
  "timer",
  "journal",
  "artifacts",
  "soma_status",
  "soma_last_errors",
  "sms_status",
]);

/** Long-running actions: 30 min TTL (auto_finish can run ~25 min). */
const LONG_RUNNING_ACTIONS = new Set([
  "soma_kajabi_auto_finish",
  "soma_run_to_done",
  "soma_kajabi_reauth_and_resume",
  "soma_kajabi_capture_interactive",
  "deploy_and_verify",
]);

const STALE_MS_NORMAL = 5 * 60 * 1000;
const STALE_MS_LONG = 30 * 60 * 1000;
/** When heartbeat exists, use 3 min — script updates every 10–15s during polling. */
const STALE_MS_HEARTBEAT = 3 * 60 * 1000;

function getStaleMs(actionName: string): number {
  return LONG_RUNNING_ACTIONS.has(actionName) ? STALE_MS_LONG : STALE_MS_NORMAL;
}

function getStaleMsFromFile(actionName: string, fileLock: LockFileShape | null): number {
  if (fileLock?.last_heartbeat_at) return STALE_MS_HEARTBEAT;
  return getStaleMs(actionName);
}

function getLockFilePath(actionName: string): string {
  const root = process.env.OPENCLAW_ARTIFACTS_ROOT || join(process.env.OPENCLAW_REPO_ROOT || process.cwd(), "artifacts");
  const dir = join(root, ".locks");
  try {
    mkdirSync(dir, { recursive: true });
  } catch {
    // best-effort
  }
  return join(dir, `${actionName.replace(/[^a-zA-Z0-9_.-]/g, "_")}.json`);
}

function readLockFile(actionName: string): LockFileShape | null {
  try {
    const path = getLockFilePath(actionName);
    if (!existsSync(path)) return null;
    const raw = readFileSync(path, "utf-8");
    const data = JSON.parse(raw) as LockFileShape;
    if (typeof data.run_id === "string" && typeof data.started_at === "string") return data;
    return null;
  } catch {
    return null;
  }
}

function writeLockFile(actionName: string, runId: string, startedAt: number): void {
  try {
    const path = getLockFilePath(actionName);
    const now = new Date(startedAt).toISOString();
    const payload: LockFileShape = {
      run_id: runId,
      started_at: now,
      last_heartbeat_at: now,
      state: "running",
    };
    writeFileSync(path, JSON.stringify(payload, null, 2), "utf-8");
  } catch {
    // best-effort; in-memory lock still works
  }
}

function deleteLockFile(actionName: string): void {
  try {
    const path = getLockFilePath(actionName);
    if (existsSync(path)) unlinkSync(path);
  } catch {
    // best-effort
  }
}

function generateLockRunId(): string {
  const ts = new Date().toISOString().replace(/[-:T]/g, "").slice(0, 14);
  const rand = randomBytes(2).toString("hex");
  return `${ts}-${rand}`;
}

function isStale(acquiredAt: number, actionName: string, fileLock?: LockFileShape | null): boolean {
  const staleMs = fileLock ? getStaleMsFromFile(actionName, fileLock) : getStaleMs(actionName);
  const ts = fileLock?.last_heartbeat_at ? new Date(fileLock.last_heartbeat_at).getTime() : acquiredAt;
  return Date.now() - ts > staleMs;
}

/**
 * Attempt to acquire a lock for the given action.
 * Returns { acquired, runId?, existing? }. When acquired, runId is set for the in-flight run.
 * When not acquired, existing ALWAYS has runId and startedAt for join semantics (never empty).
 */
export function acquireLock(actionName: string): LockAcquireResult {
  if (CONCURRENT_ALLOWED.has(actionName)) {
    return { acquired: true, runId: generateLockRunId() };
  }

  // 1. Check in-memory first
  let existing = locks.get(actionName);
  const fileLock = readLockFile(actionName);
  if (existing) {
    if (isStale(existing.acquiredAt, actionName, fileLock)) {
      locks.delete(actionName);
      deleteLockFile(actionName);
    } else {
      return {
        acquired: false,
        existing: { runId: existing.runId, startedAt: existing.acquiredAt },
      };
    }
  }

  // 2. Check file (for restart / multi-instance)
  const fileLock2 = readLockFile(actionName);
  if (fileLock2) {
    const startedAt = new Date(fileLock2.started_at).getTime();
    if (!isStale(startedAt, actionName, fileLock2)) {
      // Sync in-memory
      locks.set(actionName, { acquiredAt: startedAt, runId: fileLock2.run_id });
      return {
        acquired: false,
        existing: { runId: fileLock2.run_id, startedAt },
      };
    }
    deleteLockFile(actionName);
  }

  const runId = generateLockRunId();
  const now = Date.now();
  locks.set(actionName, { acquiredAt: now, runId });
  writeLockFile(actionName, runId, now);
  return { acquired: true, runId };
}

/**
 * Release the lock for the given action.
 */
export function releaseLock(actionName: string): void {
  locks.delete(actionName);
  deleteLockFile(actionName);
}

/**
 * Force-clear a lock (for unlock action). Only safe when no active run exists.
 */
export function forceClearLock(actionName: string): boolean {
  if (CONCURRENT_ALLOWED.has(actionName)) return true;
  locks.delete(actionName);
  deleteLockFile(actionName);
  return true;
}

/**
 * Check if an action is currently locked.
 */
export function isLocked(actionName: string): boolean {
  if (CONCURRENT_ALLOWED.has(actionName)) return false;
  const fileLock = readLockFile(actionName);
  const entry = locks.get(actionName);
  if (entry && !isStale(entry.acquiredAt, actionName, fileLock)) return true;
  if (fileLock) {
    const startedAt = new Date(fileLock.started_at).getTime();
    return !isStale(startedAt, actionName, fileLock);
  }
  return false;
}

/**
 * Get lock info for an action (for 409 response: joinable single-flight).
 * Returns { action, active_run_id, started_at } or null if not locked.
 * ALWAYS includes active_run_id when locked (never empty).
 */
export function getLockInfo(actionName: string): {
  action: string;
  active_run_id: string;
  started_at: string;
  last_heartbeat_at?: string;
  artifact_dir?: string;
} | null {
  if (CONCURRENT_ALLOWED.has(actionName)) return null;

  let runId: string | undefined;
  let startedAt: number | undefined;

  const fileLockForInfo = readLockFile(actionName);
  const entry = locks.get(actionName);
  if (entry && !isStale(entry.acquiredAt, actionName, fileLockForInfo)) {
    runId = entry.runId;
    startedAt = entry.acquiredAt;
  } else {
    if (fileLockForInfo) {
      const t = new Date(fileLockForInfo.started_at).getTime();
      if (!isStale(t, actionName, fileLockForInfo)) {
        runId = fileLockForInfo.run_id;
        startedAt = t;
      }
    }
  }

  if (!runId || startedAt == null) return null;
  const info: { action: string; active_run_id: string; started_at: string; last_heartbeat_at?: string; artifact_dir?: string } = {
    action: actionName,
    active_run_id: runId,
    started_at: new Date(startedAt).toISOString(),
  };
  if (fileLockForInfo?.last_heartbeat_at) info.last_heartbeat_at = fileLockForInfo.last_heartbeat_at;
  if (fileLockForInfo?.artifact_dir) info.artifact_dir = fileLockForInfo.artifact_dir;
  return info;
}

/**
 * Get all currently held locks (for status display).
 */
export function getActiveLocks(): Array<{
  action: string;
  runId: string;
  acquiredAt: number;
  elapsed_ms: number;
}> {
  const now = Date.now();
  const out: Array<{ action: string; runId: string; acquiredAt: number; elapsed_ms: number }> = [];
  for (const [action, entry] of Array.from(locks.entries())) {
    if (CONCURRENT_ALLOWED.has(action) || isStale(entry.acquiredAt, action, readLockFile(action))) continue;
    out.push({
      action,
      runId: entry.runId,
      acquiredAt: entry.acquiredAt,
      elapsed_ms: now - entry.acquiredAt,
    });
  }
  return out;
}
