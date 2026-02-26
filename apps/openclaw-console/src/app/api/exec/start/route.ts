/**
 * POST /api/exec/start
 *
 * Async start: returns run_id immediately, never blocks.
 * For long-running actions only. Poll GET /api/runs?id=<run_id> for status.
 *
 * Input: { action: string, project_id?: string, args?: object }
 * Output: { ok: true, run_id, status: "queued"|"running", artifact_dir? }
 */

import { NextRequest, NextResponse } from "next/server";
import { join } from "path";
import { mkdirSync, writeFileSync, existsSync } from "fs";
import { executeAction, checkConnectivity, getHostdUrl, LONG_RUNNING_ACTIONS } from "@/lib/hostd";
import { acquireLock, releaseLock, getLockInfo } from "@/lib/action-lock";
import { writeAuditEntry, deriveActor, hashParams } from "@/lib/audit";
import {
  buildRunRecord,
  buildRunRecordStart,
  writeRunRecord,
} from "@/lib/run-recorder";

export const runtime = "nodejs";

function validateOrigin(req: NextRequest): NextResponse | null {
  const port = process.env.OPENCLAW_CONSOLE_PORT || process.env.PORT || "8787";
  const allowed = new Set([
    `http://127.0.0.1:${port}`,
    `http://localhost:${port}`,
  ]);
  const tsHost = process.env.OPENCLAW_TAILSCALE_HOSTNAME;
  if (tsHost) allowed.add(`https://${tsHost}`);
  const origin = req.headers.get("origin");
  const secFetchSite = req.headers.get("sec-fetch-site");
  const host = req.headers.get("host") ?? "";
  if (origin && allowed.has(origin)) return null;
  if (secFetchSite === "same-origin") return null;
  if (!origin && (host.startsWith("127.0.0.1") || host.startsWith("localhost"))) return null;
  if (process.env.OPENCLAW_TRUST_TAILSCALE === "1" && host.includes(".ts.net")) return null;
  return NextResponse.json({ ok: false, error: "Forbidden" }, { status: 403 });
}

export async function POST(req: NextRequest) {
  const originError = validateOrigin(req);
  if (originError) return originError;

  let body: Record<string, unknown>;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ ok: false, error: "Invalid or missing JSON body." }, { status: 400 });
  }

  const actionName = body?.action;
  if (!actionName || typeof actionName !== "string") {
    return NextResponse.json({ ok: false, error: 'Missing or invalid "action" field.' }, { status: 400 });
  }

  if (!LONG_RUNNING_ACTIONS.has(actionName)) {
    return NextResponse.json(
      {
        ok: false,
        error: `Action "${actionName}" is not long-running. Use POST /api/exec for synchronous execution.`,
        use_sync: "/api/exec",
      },
      { status: 400 }
    );
  }

  const lockResult = acquireLock(actionName);
  if (!lockResult.acquired) {
    const lockInfo = getLockInfo(actionName);
    const runId = lockResult.existing?.runId ?? lockInfo?.active_run_id;
    return NextResponse.json(
      {
        ok: false,
        error_class: "ALREADY_RUNNING",
        action: actionName,
        active_run_id: runId ?? "(unknown)",
        active_run_status: "running",
        started_at: lockInfo?.started_at,
        artifact_dir: lockInfo?.artifact_dir,
        message: "Join via GET /api/runs?id=<active_run_id>",
      },
      { status: 409 }
    );
  }

  const runId = lockResult.runId!;
  const actor = deriveActor(req.headers.get("x-openclaw-token"));
  const startedAt = new Date();

  const hostdProbe = await checkConnectivity();
  if (!hostdProbe.ok) {
    const url = getHostdUrl() ?? "(OPENCLAW_HOSTD_URL not set)";
    const errorSummary = `Host Executor unreachable at ${url}.`;
    releaseLock(actionName);
    return NextResponse.json(
      {
        ok: false,
        error_class: "HOSTD_UNREACHABLE",
        error_summary: errorSummary,
        action: actionName,
        run_id: runId,
      },
      { status: 502 }
    );
  }

  writeRunRecord(buildRunRecordStart(actionName, startedAt, runId, undefined, "running"));

  void (async () => {
    try {
      const result = await executeAction(actionName);
      const finishedAt = new Date();

      let errorForRecord = result.error || null;
      if (result.stdout) {
        try {
          const parsed = JSON.parse(result.stdout.trim().split("\n").pop() || "{}");
          if (parsed.error_class) {
            errorForRecord = `error_class: ${parsed.error_class}`;
          }
        } catch {
          /* ignore */
        }
      }

      writeAuditEntry({
        timestamp: finishedAt.toISOString(),
        actor,
        action_name: actionName,
        params_hash: hashParams({ action: actionName }),
        exit_code: result.exitCode,
        duration_ms: result.durationMs,
        ...(errorForRecord && { error: errorForRecord }),
      });

      const runRecord = buildRunRecord(
        actionName,
        startedAt,
        finishedAt,
        result.exitCode,
        result.ok,
        errorForRecord,
        runId,
        undefined,
        result.artifact_dir ?? undefined,
        (result as { error_class?: string }).error_class ?? undefined
      );
      writeRunRecord(runRecord);
    } catch (err) {
      const finishedAt = new Date();
      const errorMsg = err instanceof Error ? err.message : String(err);
      writeAuditEntry({
        timestamp: finishedAt.toISOString(),
        actor,
        action_name: actionName,
        params_hash: hashParams({ action: actionName }),
        exit_code: null,
        duration_ms: finishedAt.getTime() - startedAt.getTime(),
        error: errorMsg,
      });
      writeRunRecord(
        buildRunRecord(actionName, startedAt, finishedAt, null, false, errorMsg, runId)
      );
    } finally {
      releaseLock(actionName);
    }
  })();

  return NextResponse.json(
    {
      ok: true,
      run_id: runId,
      status: "running",
      message: "Poll GET /api/runs?id=" + runId + " for status",
    },
    { status: 202 }
  );
}
