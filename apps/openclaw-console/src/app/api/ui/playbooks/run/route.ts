import { NextRequest, NextResponse } from "next/server";
import { deriveActor } from "@/lib/audit";
import { buildInboxSummary } from "@/lib/inbox-summary";
import { dispatchPlaybook } from "@/lib/playbook-runner";
import { decide } from "@/lib/policy";
import { getPlaybookById } from "@/lib/plugins";

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

export async function POST(req: NextRequest) {
  const originError = validateOrigin(req);
  if (originError) return originError;

  let body: { playbook_id?: string; project_id?: string; user_role?: string; confirm_phrase?: string };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ ok: false, error: "Invalid JSON body." }, { status: 400 });
  }

  if (!body.playbook_id || !body.project_id) {
    return NextResponse.json(
      { ok: false, error: 'Body must include "playbook_id" and "project_id".' },
      { status: 400 }
    );
  }

  const playbook = getPlaybookById(body.playbook_id);
  if (!playbook || playbook.project_id !== body.project_id) {
    return NextResponse.json(
      { ok: false, error: "Playbook not found for project.", error_class: "PLAYBOOK_NOT_FOUND" },
      { status: 404 }
    );
  }

  const summary = buildInboxSummary(body.project_id);
  const project = summary.projects.find((item) => item.project_id === body.project_id);
  if (!project) {
    return NextResponse.json(
      { ok: false, error: "Project summary unavailable.", error_class: "PROJECT_NOT_FOUND" },
      { status: 404 }
    );
  }

  const statePack = {
    project_id: project.project_id,
    approvals_pending: project.approvals_pending,
    needs_human: project.needs_human,
    core_status: project.core_status,
    optional_status: project.optional_status,
    business_dod_pass: project.business_dod_pass,
  };
  const userRole = body.user_role === "admin" ? "admin" : "operator";
  const policy = decide(playbook, statePack, summary.autonomy_mode.mode, userRole, {
    source: "manual",
    confirm_phrase: body.confirm_phrase ?? null,
    is_privileged: Boolean(req.headers.get("x-openclaw-token")) || userRole === "admin",
  });
  const actor = deriveActor(req.headers.get("x-openclaw-token"));
  const result = await dispatchPlaybook({
    playbook,
    state_pack: statePack,
    policy,
    autonomy_mode: summary.autonomy_mode.mode,
    actor,
    user_role: userRole,
    request_source: "manual",
  });

  const status =
    result.status === "APPROVAL_REQUIRED" || result.status === "RUNNING" || result.status === "JOINED_EXISTING_RUN"
      ? 202
      : result.status === "BREAK_GLASS_REQUIRED"
        ? 409
        : result.ok
          ? 200
          : result.error_class === "HOSTD_UNREACHABLE"
            ? 502
            : 400;

  return NextResponse.json(
    {
      ...result,
      policy,
    },
    { status }
  );
}
