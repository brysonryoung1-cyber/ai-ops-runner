import { createHash } from "crypto";
import { existsSync, readFileSync } from "fs";
import { join } from "path";
import { readJsonFile, writeJsonAtomic } from "./server-artifacts";

export type NotificationEventType =
  | "APPROVAL_CREATED"
  | "APPROVAL_RESOLVED"
  | "PLAYBOOK_RUN_PASS"
  | "PLAYBOOK_RUN_FAIL";

interface NotificationStateFile {
  events: Record<string, string>;
}

export interface NotificationResult {
  needed: boolean;
  sent: boolean;
  deduped: boolean;
  state_hash: string;
  message: string;
  error_class?: string;
}

function getNotificationStatePath(): string {
  const artifactsRoot = process.env.OPENCLAW_ARTIFACTS_ROOT || join(process.env.OPENCLAW_REPO_ROOT || process.cwd(), "artifacts");
  return join(artifactsRoot, "system", "notification_router_state.json");
}

function readNotificationState(): NotificationStateFile {
  return readJsonFile<NotificationStateFile>(getNotificationStatePath()) ?? { events: {} };
}

function writeNotificationState(state: NotificationStateFile): void {
  writeJsonAtomic(getNotificationStatePath(), state);
}

function resolveDiscordWebhookUrl(): { url: string | null; source: string } {
  for (const envName of ["OPENCLAW_DISCORD_WEBHOOK_URL", "DISCORD_WEBHOOK_URL", "DISCORD_WEBHOOK"]) {
    const value = process.env[envName]?.trim();
    if (value) return { url: value, source: "env" };
  }
  for (const path of [
    "/etc/ai-ops-runner/secrets/discord_webhook_url",
    "/etc/ai-ops-runner/config/discord_webhook_url",
  ]) {
    try {
      if (!existsSync(path)) continue;
      const value = readFileSync(path, "utf-8").trim();
      if (value) return { url: value, source: "file" };
    } catch {
      // ignore
    }
  }
  return { url: null, source: "missing" };
}

export function buildNotificationStateHash(payload: unknown): string {
  return createHash("sha256")
    .update(JSON.stringify(payload))
    .digest("hex");
}

function buildHqBaseUrl(): string {
  return (
    process.env.OPENCLAW_CANONICAL_URL ||
    process.env.OPENCLAW_FRONTDOOR_BASE_URL ||
    process.env.OPENCLAW_PUBLIC_BASE_URL ||
    "http://127.0.0.1:8787"
  ).replace(/\/$/, "");
}

export async function sendTransitionNotification(input: {
  project_id: string;
  event_type: NotificationEventType;
  state_hash: string;
  summary: string;
  proof_path?: string | null;
  hq_path?: string | null;
}): Promise<NotificationResult> {
  const dedupeKey = `${input.project_id}:${input.event_type}`;
  const state = readNotificationState();
  if (state.events[dedupeKey] === input.state_hash) {
    return {
      needed: true,
      sent: false,
      deduped: true,
      state_hash: input.state_hash,
      message: "Notification already sent for this state hash.",
    };
  }

  const webhook = resolveDiscordWebhookUrl();
  if (!webhook.url) {
    return {
      needed: false,
      sent: false,
      deduped: false,
      state_hash: input.state_hash,
      message: "Discord webhook not configured.",
      error_class: "DISCORD_WEBHOOK_MISSING",
    };
  }

  const hqLink = input.hq_path ? `${buildHqBaseUrl()}${input.hq_path}` : null;
  const content = [
    `OpenClaw ${input.event_type}`,
    `project: ${input.project_id}`,
    input.summary,
    input.proof_path ? `proof: ${input.proof_path}` : null,
    hqLink ? `hq: ${hqLink}` : null,
  ]
    .filter(Boolean)
    .join("\n");

  try {
    const response = await fetch(webhook.url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    });
    if (!response.ok) {
      return {
        needed: true,
        sent: false,
        deduped: false,
        state_hash: input.state_hash,
        message: `Discord returned HTTP ${response.status}.`,
        error_class: "DISCORD_HTTP_ERROR",
      };
    }
    state.events[dedupeKey] = input.state_hash;
    writeNotificationState(state);
    return {
      needed: true,
      sent: true,
      deduped: false,
      state_hash: input.state_hash,
      message: "Notification sent.",
    };
  } catch (error) {
    return {
      needed: true,
      sent: false,
      deduped: false,
      state_hash: input.state_hash,
      message: error instanceof Error ? error.message : String(error),
      error_class: "DISCORD_REQUEST_FAILED",
    };
  }
}
