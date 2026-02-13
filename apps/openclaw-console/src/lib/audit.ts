/**
 * Append-only audit log for console actions.
 *
 * Each action execution produces a log entry with:
 *   - timestamp
 *   - actor (token fingerprint, never the actual token)
 *   - action_name
 *   - params_hash
 *   - exit_code
 *   - duration_ms
 *   - artifact/job pointer
 *
 * Storage: append-only JSONL file at data/audit.jsonl
 * (Postgres upgrade path documented but file-based is appropriate for
 *  single-node deployment where Postgres may not be available.)
 */

import { appendFileSync, mkdirSync, existsSync, readFileSync } from "fs";
import { join } from "path";
import { createHash } from "crypto";

const DATA_DIR = join(process.cwd(), "data");
const AUDIT_FILE = join(DATA_DIR, "audit.jsonl");

export interface AuditEntry {
  timestamp: string;
  actor: string;
  action_name: string;
  params_hash: string;
  exit_code: number | null;
  duration_ms: number;
  artifact?: string;
  error?: string;
}

/**
 * Derive a non-reversible actor fingerprint from the auth token.
 * Returns "anonymous" if no token is provided.
 * NEVER logs the actual token.
 */
export function deriveActor(token: string | null | undefined): string {
  if (!token) return "anonymous";
  const hash = createHash("sha256").update(token).digest("hex");
  return `tok_${hash.substring(0, 8)}`;
}

/**
 * Hash action parameters for audit trail (no raw params stored).
 */
export function hashParams(params: Record<string, unknown>): string {
  const sorted = JSON.stringify(params, Object.keys(params).sort());
  return createHash("sha256").update(sorted).digest("hex").substring(0, 12);
}

/**
 * Append an audit entry to the log file.
 * Creates the data directory if it doesn't exist.
 * Never throws — swallows errors to avoid breaking action execution.
 */
export function writeAuditEntry(entry: AuditEntry): void {
  try {
    if (!existsSync(DATA_DIR)) {
      mkdirSync(DATA_DIR, { recursive: true });
    }
    const line = JSON.stringify(entry) + "\n";
    appendFileSync(AUDIT_FILE, line, { encoding: "utf-8" });
  } catch (err) {
    // Audit write failure must NOT break action execution
    console.error(
      `[AUDIT] Failed to write entry: ${err instanceof Error ? err.message : String(err)}`
    );
  }
}

/**
 * Read the last N audit entries (for display in console UI).
 */
export function readAuditEntries(limit: number = 50): AuditEntry[] {
  try {
    if (!existsSync(AUDIT_FILE)) return [];
    const content = readFileSync(AUDIT_FILE, "utf-8");
    const lines = content.trim().split("\n").filter(Boolean);
    const entries = lines
      .map((line) => {
        try {
          return JSON.parse(line) as AuditEntry;
        } catch {
          return null;
        }
      })
      .filter((e): e is AuditEntry => e !== null);
    return entries.slice(-limit);
  } catch {
    return [];
  }
}
