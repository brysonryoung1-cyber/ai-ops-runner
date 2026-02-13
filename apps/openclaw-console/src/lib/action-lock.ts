/**
 * Action Lock — prevents overlapping execution of the same action.
 *
 * Uses an in-memory map of action names to lock state.
 * Lock is acquired before execution and released after.
 * If a lock cannot be acquired, the action is rejected with 409 Conflict.
 *
 * Thread-safe for single-process Node.js (one event loop).
 */

interface LockEntry {
  acquiredAt: number;
}

const locks = new Map<string, LockEntry>();

/** Actions that are safe to run concurrently (read-only) */
const CONCURRENT_ALLOWED: ReadonlySet<string> = new Set([
  "ports",
  "timer",
  "journal",
  "artifacts",
]);

/**
 * Attempt to acquire a lock for the given action.
 * Returns true if lock acquired, false if action is already running.
 */
export function acquireLock(actionName: string): boolean {
  const existing = locks.get(actionName);

  if (existing) {
    // Allow concurrent for read-only actions
    if (CONCURRENT_ALLOWED.has(actionName)) {
      return true;
    }

    // Check for stale locks (> 5 minutes = likely orphaned)
    const elapsed = Date.now() - existing.acquiredAt;
    if (elapsed > 5 * 60 * 1000) {
      // Stale lock — force release
      locks.delete(actionName);
    } else {
      return false; // Lock held
    }
  }

  locks.set(actionName, {
    acquiredAt: Date.now(),
  });
  return true;
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
 * Get all currently held locks (for status display).
 */
export function getActiveLocks(): Array<{
  action: string;
  acquiredAt: number;
  elapsed_ms: number;
}> {
  const now = Date.now();
  return Array.from(locks.entries()).map(([action, entry]) => ({
    action,
    acquiredAt: entry.acquiredAt,
    elapsed_ms: now - entry.acquiredAt,
  }));
}
