/**
 * Project registry loader + validator.
 *
 * Reads config/projects.json and validates against the schema.
 * Fail-closed: if the file is missing, invalid, or any project
 * fails validation, the entire registry is rejected.
 */

import { readFileSync } from "fs";
import { join } from "path";

// ── Types ──────────────────────────────────────────────────────

export interface ProjectSchedule {
  workflow: string;
  cron: string;
  label: string;
}

export interface NotificationFlags {
  on_success: boolean;
  on_failure: boolean;
  on_recovery: boolean;
  channels: string[];
}

export interface Project {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
  workflows: string[];
  schedules: ProjectSchedule[];
  notification_flags: NotificationFlags;
  tags: string[];
}

export interface ProjectRegistry {
  version: number;
  projects: Project[];
}

// ── Validation ─────────────────────────────────────────────────

const PROJECT_ID_RE = /^[a-z][a-z0-9_]{1,63}$/;

export class ProjectValidationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ProjectValidationError";
  }
}

/**
 * Validate a single project entry. Throws ProjectValidationError on
 * any schema violation. Fail-closed: every field is checked.
 */
export function validateProject(p: unknown, index: number): Project {
  if (!p || typeof p !== "object" || Array.isArray(p)) {
    throw new ProjectValidationError(
      `Project at index ${index}: must be a non-null object`
    );
  }

  const obj = p as Record<string, unknown>;

  // id
  if (typeof obj.id !== "string" || !PROJECT_ID_RE.test(obj.id)) {
    throw new ProjectValidationError(
      `Project at index ${index}: "id" must match ${PROJECT_ID_RE} (got ${JSON.stringify(obj.id)})`
    );
  }

  // name
  if (typeof obj.name !== "string" || obj.name.length < 1 || obj.name.length > 128) {
    throw new ProjectValidationError(
      `Project "${obj.id}": "name" must be a string of 1-128 chars`
    );
  }

  // description
  if (typeof obj.description !== "string" || obj.description.length > 512) {
    throw new ProjectValidationError(
      `Project "${obj.id}": "description" must be a string of 0-512 chars`
    );
  }

  // enabled
  if (typeof obj.enabled !== "boolean") {
    throw new ProjectValidationError(
      `Project "${obj.id}": "enabled" must be a boolean`
    );
  }

  // workflows
  if (!Array.isArray(obj.workflows)) {
    throw new ProjectValidationError(
      `Project "${obj.id}": "workflows" must be an array`
    );
  }
  for (const w of obj.workflows) {
    if (typeof w !== "string" || w.length < 1) {
      throw new ProjectValidationError(
        `Project "${obj.id}": each workflow must be a non-empty string`
      );
    }
  }

  // schedules
  if (!Array.isArray(obj.schedules)) {
    throw new ProjectValidationError(
      `Project "${obj.id}": "schedules" must be an array`
    );
  }
  for (const s of obj.schedules) {
    if (!s || typeof s !== "object" || Array.isArray(s)) {
      throw new ProjectValidationError(
        `Project "${obj.id}": each schedule must be an object`
      );
    }
    const sched = s as Record<string, unknown>;
    if (typeof sched.workflow !== "string" || sched.workflow.length < 1) {
      throw new ProjectValidationError(
        `Project "${obj.id}": schedule.workflow must be a non-empty string`
      );
    }
    if (typeof sched.cron !== "string" || sched.cron.length < 5) {
      throw new ProjectValidationError(
        `Project "${obj.id}": schedule.cron must be a valid cron expression`
      );
    }
    if (typeof sched.label !== "string" || sched.label.length < 1) {
      throw new ProjectValidationError(
        `Project "${obj.id}": schedule.label must be a non-empty string`
      );
    }
  }

  // notification_flags
  if (!obj.notification_flags || typeof obj.notification_flags !== "object" || Array.isArray(obj.notification_flags)) {
    throw new ProjectValidationError(
      `Project "${obj.id}": "notification_flags" must be an object`
    );
  }
  const nf = obj.notification_flags as Record<string, unknown>;
  if (typeof nf.on_success !== "boolean") {
    throw new ProjectValidationError(
      `Project "${obj.id}": notification_flags.on_success must be boolean`
    );
  }
  if (typeof nf.on_failure !== "boolean") {
    throw new ProjectValidationError(
      `Project "${obj.id}": notification_flags.on_failure must be boolean`
    );
  }
  if (typeof nf.on_recovery !== "boolean") {
    throw new ProjectValidationError(
      `Project "${obj.id}": notification_flags.on_recovery must be boolean`
    );
  }
  if (!Array.isArray(nf.channels)) {
    throw new ProjectValidationError(
      `Project "${obj.id}": notification_flags.channels must be an array`
    );
  }
  const validChannels = ["pushover", "sms", "email"];
  const validChannelSet = new Set(validChannels);
  for (const ch of nf.channels) {
    if (typeof ch !== "string" || !validChannelSet.has(ch)) {
      throw new ProjectValidationError(
        `Project "${obj.id}": notification channel must be one of: ${validChannels.join(", ")}`
      );
    }
  }

  // tags
  if (!Array.isArray(obj.tags)) {
    throw new ProjectValidationError(
      `Project "${obj.id}": "tags" must be an array`
    );
  }
  for (const t of obj.tags) {
    if (typeof t !== "string" || t.length < 1) {
      throw new ProjectValidationError(
        `Project "${obj.id}": each tag must be a non-empty string`
      );
    }
  }

  return {
    id: obj.id as string,
    name: obj.name as string,
    description: obj.description as string,
    enabled: obj.enabled as boolean,
    workflows: obj.workflows as string[],
    schedules: (obj.schedules as Record<string, unknown>[]).map((s) => ({
      workflow: s.workflow as string,
      cron: s.cron as string,
      label: s.label as string,
    })),
    notification_flags: {
      on_success: nf.on_success as boolean,
      on_failure: nf.on_failure as boolean,
      on_recovery: nf.on_recovery as boolean,
      channels: nf.channels as string[],
    },
    tags: obj.tags as string[],
  };
}

/**
 * Validate and parse a full project registry.
 * Fail-closed: rejects on any error. Also checks for duplicate IDs.
 */
export function validateRegistry(raw: unknown): ProjectRegistry {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    throw new ProjectValidationError("Registry must be a non-null object");
  }

  const obj = raw as Record<string, unknown>;

  if (obj.version !== 1) {
    throw new ProjectValidationError(
      `Registry version must be 1 (got ${JSON.stringify(obj.version)})`
    );
  }

  if (!Array.isArray(obj.projects)) {
    throw new ProjectValidationError('"projects" must be an array');
  }

  const projects: Project[] = [];
  const seenIds = new Set<string>();

  for (let i = 0; i < obj.projects.length; i++) {
    const p = validateProject(obj.projects[i], i);

    if (seenIds.has(p.id)) {
      throw new ProjectValidationError(
        `Duplicate project ID: "${p.id}"`
      );
    }
    seenIds.add(p.id);
    projects.push(p);
  }

  return { version: 1, projects };
}

// ── Loader ─────────────────────────────────────────────────────

/**
 * Resolve the path to config/projects.json.
 * Works from both the console app dir and the repo root.
 */
function resolveProjectsPath(): string {
  // Try relative to the repo root first (two levels up from apps/openclaw-console)
  const candidates = [
    join(process.cwd(), "config", "projects.json"),
    join(process.cwd(), "..", "..", "config", "projects.json"),
    join(__dirname, "..", "..", "..", "..", "..", "config", "projects.json"),
  ];

  for (const candidate of candidates) {
    try {
      readFileSync(candidate, "utf-8");
      return candidate;
    } catch {
      // Try next
    }
  }

  // Fallback: assume repo root
  return candidates[0];
}

/**
 * Load and validate the project registry from disk.
 * Fail-closed: throws on any error (file missing, parse error, validation).
 */
export function loadProjectRegistry(): ProjectRegistry {
  const filePath = resolveProjectsPath();
  const raw = readFileSync(filePath, "utf-8");
  const data = JSON.parse(raw);
  return validateRegistry(data);
}

/**
 * Safe loader that returns null on failure instead of throwing.
 * Logs the error to stderr. Use this for UI rendering where
 * a validation error shouldn't crash the page.
 */
export function loadProjectRegistrySafe(): ProjectRegistry | null {
  try {
    return loadProjectRegistry();
  } catch (err) {
    console.error(
      `[OpenClaw HQ] Failed to load project registry: ${
        err instanceof Error ? err.message : String(err)
      }`
    );
    return null;
  }
}
