import { NextRequest, NextResponse } from "next/server";
import { join } from "path";
import { deriveActor } from "@/lib/audit";
import { readApproval, resolveApproval } from "@/lib/approvals";
import { buildInboxSummary } from "@/lib/inbox-summary";
import { dispatchPlaybook } from "@/lib/playbook-runner";
import { PolicyDecision } from "@/lib/playbooks";
import { getPlaybookById } from "@/lib/plugins";
import { buildNotificationStateHash, sendTransitionNotification } from "@/lib/notifications";
import { getArtifactsRoot, writeJsonAtomic } from "@/lib/server-artifacts";

export const runtime = "nodejs";

function validateOrigin(req: NextRequest): NextResponse | null {
  const port = process.env.OPENCLAW_CONSOLE_PORT || process.env.PORT || "8787";
  const allowed = new Set([
    `http://127.0.0.1:${port}`,
    `http://localhost:${port}`,
  ]);
  if (process.env.OPENCLAW_TAILSCALE_HOSTNAME) {
    allowed.add(`https://${process.env.OPENCLAW_TAILSCALE_HOSTNAME}`);
  }
  const origin = req.headers.get("origin");
  const secFetchSite = req.headers.get("sec-fetch-site");
  const host = req.headers.get("host") ?? "";
  if (origin && allowed.has(origin)) return null;
  if (secFetchSite === "same-origin") return null;
  if (!origin && (host.startsWith("127.0.0.1") || host.startsWith("localhost"))) return null;
  return NextResponse.json({ ok: false, error: "Forbidden" }, { status: 403 });
}

function resolveProofBundlePath(proofBundle: string): string {
  return join(getArtifactsRoot(), proofBundle.replace(/^artifacts\/?/, ""));
}

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const originError = validateOrigin(req);
  if (originError) return originError;

  const { id } = await params;
  const approval = readApproval(id);
  if (!approval) {
    return NextResponse.json({ ok: false, error: "Approval not found." }, { status: 404 });
  }
  if (approval.status !== "PENDING") {
    return NextResponse.json({ ok: false, error: "Approval is no longer pending." }, { status: 409 });
  }

  let body: { note?: string; user_role?: string };
  try {
    body = await req.json();
  } catch {
    body = {};
  }

  const playbook = getPlaybookById(approval.playbook_id);
  if (!playbook) {
    return NextResponse.json({ ok: false, error: "Playbook not found." }, { status: 404 });
  }

  const summary = buildInboxSummary(approval.project_id);
  const project = summary.projects.find((item) => item.project_id === approval.project_id);
  if (!project) {
    return NextResponse.json({ ok: false, error: "Project summary unavailable." }, { status: 404 });
  }

  const policy: PolicyDecision = {
    decision: "APPROVAL",
    reason: approval.rationale,
    required_approval: true,
    allowed: true,
    guardrails: { autonomy_mode: summary.autonomy_mode.mode },
  };
  const actor = deriveActor(req.headers.get("x-openclaw-token"));
  const proofRunId = approval.proof_bundle.split("/").pop() || null;
  const result = await dispatchPlaybook({
    playbook,
    state_pack: {
      project_id: project.project_id,
      approvals_pending: project.approvals_pending,
      needs_human: project.needs_human,
      core_status: project.core_status,
      optional_status: project.optional_status,
      business_dod_pass: project.business_dod_pass,
    },
    policy,
    autonomy_mode: summary.autonomy_mode.mode,
    actor,
    user_role: body.user_role === "admin" ? "admin" : "operator",
    request_source: "approval",
    approval_id: approval.id,
    existing_playbook_run_id: proofRunId,
  });

  if (!result.ok && result.status !== "JOINED_EXISTING_RUN") {
    return NextResponse.json({ ok: false, error: result.message || "Failed to queue approval." }, { status: 502 });
  }

  const resolved = resolveApproval(id, {
    status: "APPROVED",
    resolved_at: new Date().toISOString(),
    resolved_by: actor,
    note: body.note ?? null,
    run_id: result.run_id ?? result.active_run_id ?? null,
  });
  if (resolved) {
    writeJsonAtomic(join(resolveProofBundlePath(resolved.proof_bundle), "approval_resolution.json"), {
      approval_id: resolved.id,
      status: resolved.status,
      resolved_at: resolved.resolved_at,
      resolved_by: resolved.resolved_by,
      note: resolved.note,
      run_id: resolved.run_id,
    });
    await sendTransitionNotification({
      project_id: resolved.project_id,
      event_type: "APPROVAL_RESOLVED",
      state_hash: buildNotificationStateHash({
        approval_id: resolved.id,
        status: resolved.status,
        resolved_at: resolved.resolved_at,
      }),
      summary: `${resolved.playbook_title} approved`,
      proof_path: resolved.proof_bundle,
      hq_path: "/inbox",
    }).catch(() => {});
  }

  return NextResponse.json({
    ok: true,
    approval: resolved,
    run: result,
  });
}
