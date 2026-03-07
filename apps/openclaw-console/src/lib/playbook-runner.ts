import { join } from "path";
import { acquireLock, getLockInfo, releaseLock } from "./action-lock";
import { writeAuditEntry, hashParams } from "./audit";
import { checkConnectivity, executeAction } from "./hostd";
import { LONG_RUNNING_ACTIONS } from "./hostd";
import { buildRunRecord, buildRunRecordStart, generateRunId, writeRunRecord } from "./run-recorder";
import { writeSomaLastRunIndex } from "./soma-last-run-resolver";
import { createApprovalRequest, ApprovalRecord } from "./approvals";
import { buildNotificationStateHash, sendTransitionNotification } from "./notifications";
import { Playbook, PolicyDecision } from "./playbooks";
import { StatePackShape } from "./policy";
import {
  ensureDir,
  generateStampedId,
  getArtifactsRoot,
  readJsonFile,
  toArtifactUrl,
  writeJsonAtomic,
} from "./server-artifacts";

interface DispatchContext {
  playbook: Playbook;
  state_pack: StatePackShape;
  policy: PolicyDecision;
  autonomy_mode: "ON" | "OFF";
  actor: string;
  user_role: string;
  request_source: "manual" | "approval";
  approval_id?: string | null;
  existing_playbook_run_id?: string | null;
}

interface PreparedProofBundle {
  playbook_run_id: string;
  bundle_path: string;
  bundle_url: string | null;
}

interface DispatchResult {
  ok: boolean;
  status:
    | "REVIEW_READY"
    | "APPROVAL_REQUIRED"
    | "BREAK_GLASS_REQUIRED"
    | "RUNNING"
    | "JOINED_EXISTING_RUN"
    | "FAILURE";
  playbook_run_id: string;
  proof_bundle: string;
  proof_bundle_url: string | null;
  run_id?: string | null;
  approval_id?: string | null;
  active_run_id?: string | null;
  review_url?: string | null;
  error_class?: string;
  message?: string;
}

const SOMA_RUN_ACTIONS = new Set([
  "soma_run_to_done",
  "soma_kajabi_auto_finish",
  "soma_fix_and_retry",
  "soma_novnc_oneclick_recovery",
  "soma_kajabi_reauth_and_resume",
]);

function buildBundleRoot(playbookRunId: string): string {
  return join(getArtifactsRoot(), "system", "playbook_runs", playbookRunId);
}

function parseResultPayload(stdout: string): Record<string, unknown> | null {
  const lines = String(stdout || "").trim().split("\n").reverse();
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed.startsWith("{")) continue;
    try {
      const parsed = JSON.parse(trimmed);
      return typeof parsed === "object" && parsed ? (parsed as Record<string, unknown>) : null;
    } catch {
      // ignore
    }
  }
  return null;
}

function prepareProofBundle(context: DispatchContext): PreparedProofBundle {
  const playbookRunId = context.existing_playbook_run_id || generateStampedId("playbook");
  const bundlePath = buildBundleRoot(playbookRunId);
  ensureDir(bundlePath);

  const inputsPath = join(bundlePath, "inputs.json");
  if (!readJsonFile(inputsPath)) {
    writeJsonAtomic(inputsPath, {
      requested_at: new Date().toISOString(),
      requested_by: context.actor,
      request_source: context.request_source,
      user_role: context.user_role,
      autonomy_mode: context.autonomy_mode,
      playbook: context.playbook,
      state_pack: context.state_pack,
      approval_id: context.approval_id ?? null,
    });
  }

  writeJsonAtomic(join(bundlePath, "decision.json"), {
    decided_at: new Date().toISOString(),
    policy_default: context.playbook.policy_default,
    policy: context.policy,
  });

  return {
    playbook_run_id: playbookRunId,
    bundle_path: `artifacts/system/playbook_runs/${playbookRunId}`,
    bundle_url: toArtifactUrl(`artifacts/system/playbook_runs/${playbookRunId}`),
  };
}

function writeBundleResult(
  bundle: PreparedProofBundle,
  payload: Record<string, unknown>
): void {
  const absolute = buildBundleRoot(bundle.playbook_run_id);
  writeJsonAtomic(join(absolute, "RESULT.json"), payload);
}

function writeBundleOutputs(
  bundle: PreparedProofBundle,
  payload: Record<string, unknown>
): void {
  const absolute = buildBundleRoot(bundle.playbook_run_id);
  writeJsonAtomic(join(absolute, "outputs.json"), payload);
}

function makeReviewPayload(context: DispatchContext, bundle: PreparedProofBundle): DispatchResult {
  const reviewUrl = context.playbook.project_id
    ? `/inbox?project=${encodeURIComponent(context.playbook.project_id)}`
    : "/inbox";
  const approvals = context.state_pack.approvals_pending;
  writeBundleOutputs(bundle, {
    review_url: reviewUrl,
    approvals_pending: approvals,
  });
  writeBundleResult(bundle, {
    ok: true,
    status: "REVIEW_READY",
    playbook_run_id: bundle.playbook_run_id,
    review_url: reviewUrl,
    approvals_pending: approvals,
    proof_bundle: bundle.bundle_path,
  });
  return {
    ok: true,
    status: "REVIEW_READY",
    playbook_run_id: bundle.playbook_run_id,
    proof_bundle: bundle.bundle_path,
    proof_bundle_url: bundle.bundle_url,
    review_url: reviewUrl,
    message: "Approval review context prepared.",
  };
}

function buildApprovalRecord(
  context: DispatchContext,
  bundle: PreparedProofBundle
): ApprovalRecord {
  return createApprovalRequest({
    id: generateStampedId("approval"),
    project_id: context.playbook.project_id,
    playbook_id: context.playbook.id,
    playbook_title: context.playbook.title,
    primary_action: context.playbook.primary_action,
    rationale: context.policy.reason,
    created_at: new Date().toISOString(),
    created_by: context.actor,
    proof_bundle: bundle.bundle_path,
    policy_decision: "APPROVAL",
    autonomy_mode: context.autonomy_mode,
  });
}

function finalizeFailure(
  bundle: PreparedProofBundle,
  errorClass: string,
  message: string
): DispatchResult {
  writeBundleResult(bundle, {
    ok: false,
    status: "FAILURE",
    error_class: errorClass,
    message,
    playbook_run_id: bundle.playbook_run_id,
    proof_bundle: bundle.bundle_path,
  });
  return {
    ok: false,
    status: "FAILURE",
    playbook_run_id: bundle.playbook_run_id,
    proof_bundle: bundle.bundle_path,
    proof_bundle_url: bundle.bundle_url,
    error_class: errorClass,
    message,
  };
}

async function sendApprovalNotification(
  record: ApprovalRecord,
  eventType: "APPROVAL_CREATED" | "APPROVAL_RESOLVED"
): Promise<void> {
  await sendTransitionNotification({
    project_id: record.project_id,
    event_type: eventType,
    state_hash: buildNotificationStateHash({
      eventType,
      approval_id: record.id,
      status: record.status,
      resolved_at: record.resolved_at,
    }),
    summary: `${record.playbook_title} (${record.status})`,
    proof_path: record.proof_bundle,
    hq_path: `/inbox`,
  }).catch(() => {});
}

async function runStubAction(
  context: DispatchContext,
  bundle: PreparedProofBundle
): Promise<DispatchResult> {
  const runId = generateRunId();
  const startedAt = new Date();
  writeRunRecord(buildRunRecordStart(context.playbook.primary_action, startedAt, runId, context.playbook.project_id, "running"));
  const finishedAt = new Date();
  const artifactDir = `${bundle.bundle_path}/stub_exec`;
  writeRunRecord(
    buildRunRecord(
      context.playbook.primary_action,
      startedAt,
      finishedAt,
      0,
      true,
      null,
      runId,
      context.playbook.project_id,
      artifactDir,
      null
    )
  );
  writeBundleOutputs(bundle, {
    stub: true,
    run_id: runId,
    artifact_dir: artifactDir,
    pointers: {
      run_record: `artifacts/runs/${runId}/run.json`,
      underlying_artifact_dir: artifactDir,
    },
  });
  writeBundleResult(bundle, {
    ok: true,
    status: "SUCCESS",
    stub: true,
    playbook_run_id: bundle.playbook_run_id,
    run_id: runId,
    proof_bundle: bundle.bundle_path,
  });
  return {
    ok: true,
    status: "RUNNING",
    playbook_run_id: bundle.playbook_run_id,
    proof_bundle: bundle.bundle_path,
    proof_bundle_url: bundle.bundle_url,
    run_id: runId,
    message: "Stub playbook execution completed.",
  };
}

function fireAndForgetHostdRun(
  context: DispatchContext,
  bundle: PreparedProofBundle,
  runId: string,
  startedAt: Date
): void {
  void (async () => {
    try {
      const result = await executeAction(context.playbook.primary_action, undefined, runId);
      const finishedAt = new Date();
      const parsedOutput = parseResultPayload(result.stdout);
      const parsedErrorClass = typeof parsedOutput?.error_class === "string" ? parsedOutput.error_class : null;
      const errorSummary = result.error || (parsedErrorClass ? `error_class: ${parsedErrorClass}` : null);
      writeAuditEntry({
        timestamp: finishedAt.toISOString(),
        actor: context.actor,
        action_name: context.playbook.primary_action,
        params_hash: hashParams({ playbook_id: context.playbook.id }),
        exit_code: result.exitCode,
        duration_ms: result.durationMs,
        ...(errorSummary ? { error: errorSummary } : {}),
      });
      writeRunRecord(
        buildRunRecord(
          context.playbook.primary_action,
          startedAt,
          finishedAt,
          result.exitCode,
          result.ok,
          errorSummary,
          runId,
          context.playbook.project_id,
          result.artifact_dir ?? undefined,
          result.error_class ?? parsedErrorClass ?? undefined
        )
      );
      if (SOMA_RUN_ACTIONS.has(context.playbook.primary_action)) {
        writeSomaLastRunIndex();
      }

      const ok = result.ok === true && (result.exitCode ?? 0) === 0;
      writeBundleOutputs(bundle, {
        result,
        parsed_output: parsedOutput,
        pointers: {
          run_record: `artifacts/runs/${runId}/run.json`,
          underlying_artifact_dir: result.artifact_dir ?? null,
          approval_id: context.approval_id ?? null,
        },
      });
      writeBundleResult(bundle, {
        ok,
        status: ok ? "SUCCESS" : "FAILURE",
        playbook_run_id: bundle.playbook_run_id,
        run_id: runId,
        approval_id: context.approval_id ?? null,
        proof_bundle: bundle.bundle_path,
        underlying_artifact_dir: result.artifact_dir ?? null,
        error_class: result.error_class ?? parsedErrorClass ?? null,
      });
      await sendTransitionNotification({
        project_id: context.playbook.project_id,
        event_type: ok ? "PLAYBOOK_RUN_PASS" : "PLAYBOOK_RUN_FAIL",
        state_hash: buildNotificationStateHash({
          playbook_run_id: bundle.playbook_run_id,
          run_id: runId,
          ok,
        }),
        summary: `${context.playbook.title} ${ok ? "succeeded" : "failed"} (run_id=${runId})`,
        proof_path: bundle.bundle_path,
        hq_path: `/artifacts/system/playbook_runs/${encodeURIComponent(bundle.playbook_run_id)}`,
      }).catch(() => {});
    } catch (error) {
      const finishedAt = new Date();
      const message = error instanceof Error ? error.message : String(error);
      writeAuditEntry({
        timestamp: finishedAt.toISOString(),
        actor: context.actor,
        action_name: context.playbook.primary_action,
        params_hash: hashParams({ playbook_id: context.playbook.id }),
        exit_code: null,
        duration_ms: finishedAt.getTime() - startedAt.getTime(),
        error: message,
      });
      writeRunRecord(
        buildRunRecord(
          context.playbook.primary_action,
          startedAt,
          finishedAt,
          null,
          false,
          message,
          runId,
          context.playbook.project_id
        )
      );
      writeBundleResult(bundle, {
        ok: false,
        status: "FAILURE",
        playbook_run_id: bundle.playbook_run_id,
        run_id: runId,
        proof_bundle: bundle.bundle_path,
        error_class: "PLAYBOOK_EXECUTION_FAILED",
        message,
      });
    } finally {
      releaseLock(context.playbook.primary_action);
    }
  })();
}

export async function dispatchPlaybook(context: DispatchContext): Promise<DispatchResult> {
  const bundle = prepareProofBundle(context);

  if (context.playbook.primary_action.startsWith("noop.review_approvals")) {
    return makeReviewPayload(context, bundle);
  }

  if (context.policy.decision === "APPROVAL" && !context.approval_id) {
    const approval = buildApprovalRecord(context, bundle);
    writeBundleOutputs(bundle, {
      approval_id: approval.id,
      request_path: approval.request_path,
      proof_bundle: approval.proof_bundle,
    });
    writeBundleResult(bundle, {
      ok: true,
      status: "APPROVAL_REQUIRED",
      playbook_run_id: bundle.playbook_run_id,
      approval_id: approval.id,
      proof_bundle: bundle.bundle_path,
    });
    await sendApprovalNotification(approval, "APPROVAL_CREATED");
    return {
      ok: true,
      status: "APPROVAL_REQUIRED",
      playbook_run_id: bundle.playbook_run_id,
      proof_bundle: bundle.bundle_path,
      proof_bundle_url: bundle.bundle_url,
      approval_id: approval.id,
      message: "Approval request created.",
    };
  }

  if (context.policy.decision === "BREAK_GLASS" && !context.policy.allowed) {
    writeBundleResult(bundle, {
      ok: false,
      status: "BREAK_GLASS_REQUIRED",
      playbook_run_id: bundle.playbook_run_id,
      proof_bundle: bundle.bundle_path,
      guardrails: context.policy.guardrails,
    });
    return {
      ok: false,
      status: "BREAK_GLASS_REQUIRED",
      playbook_run_id: bundle.playbook_run_id,
      proof_bundle: bundle.bundle_path,
      proof_bundle_url: bundle.bundle_url,
      error_class: "BREAK_GLASS_REQUIRED",
      message: context.policy.reason,
    };
  }

  if (process.env.OPENCLAW_UI_STUB === "1") {
    return runStubAction(context, bundle);
  }

  const connectivity = await checkConnectivity();
  if (!connectivity.ok) {
    return finalizeFailure(bundle, "HOSTD_UNREACHABLE", connectivity.error || "Host executor unreachable.");
  }

  const lock = acquireLock(context.playbook.primary_action);
  if (!lock.acquired) {
    const lockInfo = getLockInfo(context.playbook.primary_action);
    writeBundleOutputs(bundle, {
      active_run_id: lock.existing?.runId ?? lockInfo?.active_run_id ?? null,
      artifact_dir: lockInfo?.artifact_dir ?? null,
      joined_existing: true,
    });
    writeBundleResult(bundle, {
      ok: true,
      status: "JOINED_EXISTING_RUN",
      playbook_run_id: bundle.playbook_run_id,
      active_run_id: lock.existing?.runId ?? lockInfo?.active_run_id ?? null,
      proof_bundle: bundle.bundle_path,
    });
    return {
      ok: true,
      status: "JOINED_EXISTING_RUN",
      playbook_run_id: bundle.playbook_run_id,
      proof_bundle: bundle.bundle_path,
      proof_bundle_url: bundle.bundle_url,
      active_run_id: lock.existing?.runId ?? lockInfo?.active_run_id ?? null,
      message: "Joined existing run.",
    };
  }

  const runId = lock.runId ?? generateRunId();
  const startedAt = new Date();
  writeRunRecord(
    buildRunRecordStart(
      context.playbook.primary_action,
      startedAt,
      runId,
      context.playbook.project_id,
      LONG_RUNNING_ACTIONS.has(context.playbook.primary_action) ? "running" : "queued"
    )
  );
  writeBundleResult(bundle, {
    ok: true,
    status: "RUNNING",
    playbook_run_id: bundle.playbook_run_id,
    run_id: runId,
    approval_id: context.approval_id ?? null,
    proof_bundle: bundle.bundle_path,
  });
  fireAndForgetHostdRun(context, bundle, runId, startedAt);
  return {
    ok: true,
    status: "RUNNING",
    playbook_run_id: bundle.playbook_run_id,
    proof_bundle: bundle.bundle_path,
    proof_bundle_url: bundle.bundle_url,
    run_id: runId,
    approval_id: context.approval_id ?? null,
    message: "Playbook queued.",
  };
}
