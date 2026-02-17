/**
 * Action Lock — prevents overlapping execution of the same action.
 * Single-flight + join: 409 responses include active_run_id so callers can poll /api/runs?id=<run_id>.
 *
 * Uses an in-memory map of action names to lock state (action + run_id + started_at).
 * Lock is acquired before execution and released in a finally block.
 * TTL: stale locks > 5 minutes are force-released so locks cannot stick forever.
 *
 * Thread-safe for single-process Node.js (one event loop).
 */

import { randomBytes } from "crypto";

export interface LockAcquireResult {
  acquired: boolean;
  /** Set when acquired; use for buildRunRecord. */
  runId?: string;
  /** Set when not acquired; use for 409 response (joinable). */
  existing?: { runId: string; startedAt: number };
}

interface LockEntry {
  acquiredAt: number;
  runId: string;
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

const STALE_MS = 5 * 60 * 1000;

function generateLockRunId(): string {
  const ts = new Date().toISOString().replace(/[-:T]/g, "").slice(0, 14);
  const rand = randomBytes(2).toString("hex");
  return `${ts}-${rand}`;
}

/**
 * Attempt to acquire a lock for the given action.
 * Returns { acquired, runId?, existing? }. When acquired, runId is set for the in-flight run.
 * When not acquired, existing has active_run_id and started_at for join semantics.
 */
export function acquireLock(actionName: string): LockAcquireResult {
  const existing = locks.get(actionName);

  if (existing) {
    if (CONCURRENT_ALLOWED.has(actionName)) {
      return { acquired: true, runId: generateLockRunId() };
    }
    const elapsed = Date.now() - existing.acquiredAt;
    if (elapsed > STALE_MS) {
      locks.delete(actionName);
    } else {
      return {
        acquired: false,
        existing: { runId: existing.runId, startedAt: existing.acquiredAt },
      };
    }
  }

  const runId = generateLockRunId();
  locks.set(actionName, {
    acquiredAt: Date.now(),
    runId,
  });
  return { acquired: true, runId };
}

/**
 * Release the lock for the given action.
 */
export function releaseLock(actionName: string): void {
  locks.delete(actionName);
}

/**
 * Check if an action is currently locked.
 */
export function isLocked(actionName: string): boolean {
  return locks.has(actionName) && !CONCURRENT_ALLOWED.has(actionName);
}

/**
 * Get lock info for an action (for 409 response: joinable single-flight).
 * Returns { action, active_run_id, started_at } or null if not locked.
 */
export function getLockInfo(actionName: string): {
  action: string;
  active_run_id: string;
  started_at: string;
} | null {
  const entry = locks.get(actionName);
  if (!entry || CONCURRENT_ALLOWED.has(actionName)) return null;
  const elapsed = Date.now() - entry.acquiredAt;
  if (elapsed > STALE_MS) return null;
  return {
    action: actionName,
    active_run_id: entry.runId,
    started_at: new Date(entry.acquiredAt).toISOString(),
  };
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
  return Array.from(locks.entries()).map(([action, entry]) => ({
    action,
    runId: entry.runId,
    acquiredAt: entry.acquiredAt,
    elapsed_ms: now - entry.acquiredAt,
  }));
}
