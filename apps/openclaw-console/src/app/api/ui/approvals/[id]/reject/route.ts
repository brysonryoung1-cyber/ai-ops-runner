import { NextRequest, NextResponse } from "next/server";
import { join } from "path";
import { deriveActor } from "@/lib/audit";
import { readApproval, resolveApproval } from "@/lib/approvals";
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

  let body: { note?: string };
  try {
    body = await req.json();
  } catch {
    body = {};
  }

  const actor = deriveActor(req.headers.get("x-openclaw-token"));
  const resolvedAt = new Date().toISOString();
  const proofPath = resolveProofBundlePath(approval.proof_bundle);

  // Write artifacts first (idempotent: retry after partial write overwrites same content)
  writeJsonAtomic(join(proofPath, "approval_resolution.json"), {
    approval_id: approval.id,
    status: "REJECTED",
    resolved_at: resolvedAt,
    resolved_by: actor,
    note: body.note ?? null,
    run_id: null,
  });
  writeJsonAtomic(join(proofPath, "RESULT.json"), {
    ok: true,
    status: "REJECTED",
    approval_id: approval.id,
    proof_bundle: approval.proof_bundle,
  });

  const resolved = resolveApproval(id, {
    status: "REJECTED",
    resolved_at: resolvedAt,
    resolved_by: actor,
    note: body.note ?? null,
    run_id: null,
  });
  if (resolved) {
    await sendTransitionNotification({
      project_id: resolved.project_id,
      event_type: "APPROVAL_RESOLVED",
      state_hash: buildNotificationStateHash({
        approval_id: resolved.id,
        status: resolved.status,
        resolved_at: resolved.resolved_at,
      }),
      summary: `${resolved.playbook_title} rejected`,
      proof_path: resolved.proof_bundle,
      hq_path: "/inbox",
    }).catch(() => {});
  }

  return NextResponse.json({
    ok: true,
    approval: resolved,
  });
}
