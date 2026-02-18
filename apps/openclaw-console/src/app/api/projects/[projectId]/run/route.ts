import { NextRequest, NextResponse } from "next/server";
import { loadProjectRegistrySafe } from "@/lib/projects";
import { executeAction } from "@/lib/hostd";
import { acquireLock, releaseLock } from "@/lib/action-lock";
import { writeAuditEntry, deriveActor, hashParams } from "@/lib/audit";
import { buildRunRecord, writeRunRecord, generateRunId } from "@/lib/run-recorder";
import { PROJECT_ACTIONS } from "@/lib/action_registry.generated";

function validateOrigin(req: NextRequest): NextResponse | null {
  const origin = req.headers.get("origin");
  const secFetchSite = req.headers.get("sec-fetch-site");
  const host = req.headers.get("host") ?? "";
  const port = process.env.OPENCLAW_CONSOLE_PORT || process.env.PORT || "8787";
  const allowed = new Set([
    `http://127.0.0.1:${port}`,
    `http://localhost:${port}`,
  ]);
  if (process.env.OPENCLAW_TAILSCALE_HOSTNAME) {
    allowed.add(`https://${process.env.OPENCLAW_TAILSCALE_HOSTNAME}`);
  }
  if (origin && allowed.has(origin)) return null;
  if (secFetchSite === "same-origin") return null;
  if (!origin && (host.startsWith("127.0.0.1") || host.startsWith("localhost"))) return null;
  return NextResponse.json(
    { ok: false, error: "Forbidden: origin could not be verified." },
    { status: 403 }
  );
}

/** Deterministic stub responses when OPENCLAW_UI_STUB=1 (no hostd, no credentials). */
function getStubResponse(projectId: string, action: string): NextResponse {
  const runId = `stub-${generateRunId()}`;
  const artifactDir = `artifacts/runs/${runId}`;

  const stubs: Record<string, { ok: boolean; body: Record<string, unknown> }> = {
    soma_connectors_status: {
      ok: true,
      body: {
        ok: true,
        run_id: runId,
        artifact_dir: artifactDir,
        result_summary: { kajabi: "not_connected", gmail: "not_connected" },
      },
    },
    soma_kajabi_bootstrap_start: {
      ok: true,
      body: {
        ok: true,
        run_id: runId,
        artifact_dir: artifactDir,
        result_summary: "Bootstrap started (stub)",
        next_steps: {
          instruction: "Check status and finalize when ready.",
          verification_url: null,
          user_code: null,
        },
      },
    },
    soma_kajabi_bootstrap_status: {
      ok: true,
      body: {
        ok: true,
        run_id: runId,
        artifact_dir: artifactDir,
        result_summary: { status: "pending", ready_to_finalize: false },
      },
    },
    soma_kajabi_bootstrap_finalize: {
      ok: true,
      body: {
        ok: true,
        run_id: runId,
        artifact_dir: artifactDir,
        result_summary: "Bootstrap finalized (stub)",
      },
    },
    soma_kajabi_gmail_connect_start: {
      ok: true,
      body: {
        ok: true,
        run_id: runId,
        artifact_dir: artifactDir,
        result_summary: "Gmail connect started (stub)",
        next_steps: {
          instruction: "Complete OAuth in browser; then refresh status.",
          verification_url: "https://example.com/oauth (stub)",
          user_code: "STUB-CODE",
        },
      },
    },
    soma_kajabi_gmail_connect_status: {
      ok: true,
      body: {
        ok: true,
        run_id: runId,
        artifact_dir: artifactDir,
        result_summary: { status: "pending", ready_to_finalize: false },
      },
    },
    soma_kajabi_gmail_connect_finalize: {
      ok: true,
      body: {
        ok: true,
        run_id: runId,
        artifact_dir: artifactDir,
        result_summary: "Gmail connect finalized (stub)",
      },
    },
  };

  const stub = stubs[action];
  if (stub) {
    return NextResponse.json(stub.body, { status: stub.ok ? 200 : 502 });
  }
  return NextResponse.json(
    { ok: false, error_class: "ACTION_NOT_ALLOWED", message: `Stub has no fixture for action: ${action}` },
    { status: 400 }
  );
}

/**
 * POST /api/projects/[projectId]/run
 * Body: { action: string, payload?: object }
 *
 * Server-side project action runner. Validates projectId and allowlisted action,
 * executes via hostd (or returns stub when OPENCLAW_UI_STUB=1). Never returns secrets.
 */
export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ projectId: string }> }
) {
  const originError = validateOrigin(req);
  if (originError) return originError;

  const { projectId } = await params;
  if (!projectId || typeof projectId !== "string") {
    return NextResponse.json(
      { ok: false, error_class: "INVALID_PROJECT", message: "Missing or invalid projectId." },
      { status: 400 }
    );
  }

  let body: { action?: string; payload?: Record<string, unknown> };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json(
      { ok: false, error_class: "INVALID_BODY", message: "Invalid or missing JSON body." },
      { status: 400 }
    );
  }
  if (!body || typeof body !== "object" || !body.action || typeof body.action !== "string") {
    return NextResponse.json(
      { ok: false, error_class: "INVALID_BODY", message: 'Body must include "action" string.' },
      { status: 400 }
    );
  }

  const action = body.action;
  const allowed = PROJECT_ACTIONS[projectId];
  if (!allowed || !allowed.has(action)) {
    return NextResponse.json(
      {
        ok: false,
        error_class: "ACTION_NOT_ALLOWED",
        message: `Action "${action}" is not allowlisted for project "${projectId}".`,
      },
      { status: 403 }
    );
  }

  const registry = loadProjectRegistrySafe();
  if (!registry) {
    return NextResponse.json(
      { ok: false, error_class: "REGISTRY_ERROR", message: "Project registry unavailable." },
      { status: 503 }
    );
  }
  const project = registry.projects.find((p) => p.id === projectId);
  if (!project) {
    return NextResponse.json(
      { ok: false, error_class: "PROJECT_NOT_FOUND", message: "Project not found." },
      { status: 404 }
    );
  }

  if (process.env.OPENCLAW_UI_STUB === "1") {
    return getStubResponse(projectId, action);
  }

  const lockResult = acquireLock(action);
  if (!lockResult.acquired) {
    return NextResponse.json(
      {
        ok: false,
        error_class: "ALREADY_RUNNING",
        message: `Action "${action}" is already running.`,
        run_id: lockResult.existing?.runId,
      },
      { status: 409 }
    );
  }

  const runId = lockResult.runId!;
  const actor = deriveActor(req.headers.get("x-openclaw-token"));
  const startedAt = new Date();

  try {
    const result = await executeAction(action);

    if (result.error?.includes("not configured") || result.error?.includes("unreachable")) {
      const finishedAt = new Date();
      writeAuditEntry({
        timestamp: finishedAt.toISOString(),
        actor,
        action_name: action,
        params_hash: hashParams({ action }),
        exit_code: null,
        duration_ms: finishedAt.getTime() - startedAt.getTime(),
        error: result.error,
      });
      writeRunRecord(
        buildRunRecord(
          action,
          startedAt,
          finishedAt,
          null,
          false,
          result.error ?? "HOSTD_UNREACHABLE",
          runId,
          projectId
        )
      );
      releaseLock(action);
      return NextResponse.json(
        {
          ok: false,
          error_class: "HOSTD_UNREACHABLE",
          message: result.error,
          recommended_next_action: "Ensure OPENCLAW_HOSTD_URL and hostd are running.",
          run_id: runId,
          artifact_dir: result.artifact_dir,
        },
        { status: 502 }
      );
    }

    if (result.httpStatus === 423) {
      const finishedAt = new Date();
      writeAuditEntry({
        timestamp: finishedAt.toISOString(),
        actor,
        action_name: action,
        params_hash: hashParams({ action }),
        exit_code: null,
        duration_ms: finishedAt.getTime() - startedAt.getTime(),
        error: `error_class: ${result.error_class ?? "LANE_LOCKED_SOMA_FIRST"}`,
      });
      writeRunRecord(
        buildRunRecord(
          action,
          startedAt,
          finishedAt,
          null,
          false,
          result.error_class ?? "LANE_LOCKED_SOMA_FIRST",
          runId,
          projectId
        )
      );
      releaseLock(action);
      return NextResponse.json(
        {
          ok: false,
          error_class: result.error_class ?? "LANE_LOCKED_SOMA_FIRST",
          message: result.required_condition ?? "Soma-first gate.",
          run_id: runId,
          artifact_dir: result.artifact_dir,
        },
        { status: 423 }
      );
    }

    const finishedAt = new Date();
    let resultSummary: unknown = undefined;
    let nextSteps: { instruction?: string; verification_url?: string | null; user_code?: string | null } | undefined;
    let parsedErrorClass: string | undefined;
    let parsedMessage: string | undefined;
    let requirementsEndpoint: string | undefined;
    let expectedSecretPathRedacted: string | undefined;
    if (result.stdout) {
      try {
        const trimmed = result.stdout.trim();
        let parsed: Record<string, unknown>;
        try {
          parsed = JSON.parse(trimmed) as Record<string, unknown>;
        } catch {
          const lastLine = trimmed.split("\n").pop() || "{}";
          parsed = JSON.parse(lastLine) as Record<string, unknown>;
        }
        if (typeof parsed.error_class === "string") parsedErrorClass = parsed.error_class;
        if (typeof parsed.message === "string") parsedMessage = parsed.message;
        if (typeof parsed.requirements_endpoint === "string") requirementsEndpoint = parsed.requirements_endpoint;
        if (typeof parsed.expected_secret_path_redacted === "string") expectedSecretPathRedacted = parsed.expected_secret_path_redacted;
        if (parsed.result_summary != null) {
          resultSummary = parsed.result_summary;
        } else if ("kajabi" in parsed || "gmail" in parsed) {
          resultSummary = parsed;
        }
        if (parsed.next_steps && typeof parsed.next_steps === "object") {
          const ns = parsed.next_steps as Record<string, unknown>;
          nextSteps = {
            instruction: typeof ns.instruction === "string" ? ns.instruction : undefined,
            verification_url:
              typeof ns.verification_url === "string" ? ns.verification_url : null,
            user_code: typeof ns.user_code === "string" ? ns.user_code : null,
          };
        }
        if (!nextSteps) {
          const instruction = typeof parsed.message === "string" ? parsed.message : undefined;
          const verificationUrl =
            typeof parsed.verification_url === "string" ? parsed.verification_url : null;
          const userCode = typeof parsed.user_code === "string" ? parsed.user_code : null;
          if (instruction || verificationUrl || userCode) {
            nextSteps = {
              instruction,
              verification_url: verificationUrl,
              user_code: userCode,
            };
          }
        }
        if (resultSummary == null && typeof parsed.message === "string") {
          resultSummary = parsed.message;
        }
      } catch {
        resultSummary = result.stdout.slice(0, 500);
      }
    }

    writeAuditEntry({
      timestamp: finishedAt.toISOString(),
      actor,
      action_name: action,
      params_hash: hashParams({ action }),
      exit_code: result.exitCode,
      duration_ms: result.durationMs,
      ...(result.error && { error: result.error }),
    });
    writeRunRecord(
      buildRunRecord(
        action,
        startedAt,
        finishedAt,
        result.exitCode,
        result.ok,
        result.error ?? null,
        runId,
        projectId
      )
    );
    releaseLock(action);

    if (result.ok) {
      return NextResponse.json(
        {
          ok: true,
          run_id: runId,
          artifact_dir: result.artifact_dir,
          result_summary: resultSummary,
          ...(nextSteps && { next_steps: nextSteps }),
        },
        { status: 200 }
      );
    }

    const errorClass =
      (result as { error_class?: string }).error_class ??
      parsedErrorClass ??
      "ACTION_FAILED";
    const errorPayload: Record<string, unknown> = {
      ok: false,
      error_class: errorClass,
      message: parsedMessage ?? result.error ?? "Action failed.",
      run_id: runId,
      artifact_dir: result.artifact_dir,
    };
    if (requirementsEndpoint) errorPayload.requirements_endpoint = requirementsEndpoint;
    if (expectedSecretPathRedacted) errorPayload.expected_secret_path_redacted = expectedSecretPathRedacted;
    return NextResponse.json(errorPayload, { status: 502 });
  } catch (err) {
    const finishedAt = new Date();
    const errorMsg = err instanceof Error ? err.message : String(err);
    writeAuditEntry({
      timestamp: finishedAt.toISOString(),
      actor,
      action_name: action,
      params_hash: hashParams({ action }),
      exit_code: null,
      duration_ms: finishedAt.getTime() - startedAt.getTime(),
      error: errorMsg,
    });
    writeRunRecord(
      buildRunRecord(action, startedAt, finishedAt, null, false, errorMsg, runId, projectId)
    );
    releaseLock(action);
    return NextResponse.json(
      {
        ok: false,
        error_class: "UI_ACTION_FAILED",
        message: "Server error while running action.",
        run_id: runId,
        recommended_next_action: "Check server logs and telemetry artifacts.",
      },
      { status: 500 }
    );
  }
}
