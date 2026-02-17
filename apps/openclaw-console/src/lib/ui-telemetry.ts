"use client";

/**
 * Client helper for UI telemetry. Posts click and error events to /api/ui/telemetry.
 * Payload is truncated and redacted server-side; no secrets in logs/artifacts.
 */

export type TelemetryEvent = "click" | "error";

export interface TelemetryPayload {
  event: TelemetryEvent;
  page: string;
  control?: string;
  detail?: string;
  ts?: string;
}

function truncate(s: string, max: number): string {
  if (s.length <= max) return s;
  return s.slice(0, max) + "...";
}

export function postTelemetry(payload: TelemetryPayload): void {
  const body = {
    event: payload.event,
    page: truncate(payload.page, 300),
    control: payload.control ? truncate(payload.control, 200) : undefined,
    detail: payload.detail ? truncate(payload.detail, 500) : undefined,
    ts: payload.ts ?? new Date().toISOString(),
  };
  fetch("/api/ui/telemetry", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).catch(() => {
    // Fire-and-forget; avoid breaking UI
  });
}

/** Call on connector button click (page + control identify the button). */
export function telemetryClick(page: string, control: string): void {
  postTelemetry({ event: "click", page, control });
}

/** Call on unhandled exception in connectors flow. */
export function telemetryError(page: string, control: string, detail: string): void {
  postTelemetry({ event: "error", page, control, detail });
}
