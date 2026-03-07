import { join } from "path";
import { listChildDirectories, readJsonFile, toArtifactUrl, writeJsonAtomic, ensureDir } from "./server-artifacts";

export type ApprovalStatus = "PENDING" | "APPROVED" | "REJECTED";

export interface ApprovalRecord {
  id: string;
  project_id: string;
  playbook_id: string;
  playbook_title: string;
  primary_action: string;
  status: ApprovalStatus;
  rationale: string;
  created_at: string;
  created_by: string;
  resolved_at: string | null;
  resolved_by: string | null;
  note: string | null;
  proof_bundle: string;
  proof_bundle_url: string | null;
  request_path: string;
  request_url: string | null;
  resolution_path: string | null;
  resolution_url: string | null;
  policy_decision: "APPROVAL";
  autonomy_mode: "ON" | "OFF";
  run_id: string | null;
}

interface ApprovalRequestFile {
  id: string;
  project_id: string;
  playbook_id: string;
  playbook_title: string;
  primary_action: string;
  status: "PENDING";
  rationale: string;
  created_at: string;
  created_by: string;
  proof_bundle: string;
  policy_decision: "APPROVAL";
  autonomy_mode: "ON" | "OFF";
}

interface ApprovalResolutionFile {
  status: "APPROVED" | "REJECTED";
  resolved_at: string;
  resolved_by: string;
  note: string | null;
  run_id: string | null;
}

function getApprovalsRoot(): string {
  return join(process.env.OPENCLAW_ARTIFACTS_ROOT || join(process.env.OPENCLAW_REPO_ROOT || process.cwd(), "artifacts"), "system", "approvals");
}

function toApprovalRecord(id: string): ApprovalRecord | null {
  const root = join(getApprovalsRoot(), id);
  const requestPath = join(root, "request.json");
  const resolutionPath = join(root, "resolution.json");
  const request = readJsonFile<ApprovalRequestFile>(requestPath);
  if (!request) return null;
  const resolution = readJsonFile<ApprovalResolutionFile>(resolutionPath);
  return {
    id: request.id,
    project_id: request.project_id,
    playbook_id: request.playbook_id,
    playbook_title: request.playbook_title,
    primary_action: request.primary_action,
    status: resolution?.status ?? "PENDING",
    rationale: request.rationale,
    created_at: request.created_at,
    created_by: request.created_by,
    resolved_at: resolution?.resolved_at ?? null,
    resolved_by: resolution?.resolved_by ?? null,
    note: resolution?.note ?? null,
    proof_bundle: request.proof_bundle,
    proof_bundle_url: toArtifactUrl(request.proof_bundle),
    request_path: `artifacts/system/approvals/${id}/request.json`,
    request_url: toArtifactUrl(`artifacts/system/approvals/${id}/request.json`),
    resolution_path: resolution ? `artifacts/system/approvals/${id}/resolution.json` : null,
    resolution_url: resolution ? toArtifactUrl(`artifacts/system/approvals/${id}/resolution.json`) : null,
    policy_decision: "APPROVAL",
    autonomy_mode: request.autonomy_mode,
    run_id: resolution?.run_id ?? null,
  };
}

export function createApprovalRequest(input: Omit<ApprovalRequestFile, "status">): ApprovalRecord {
  const dir = join(getApprovalsRoot(), input.id);
  ensureDir(dir);
  writeJsonAtomic(join(dir, "request.json"), { ...input, status: "PENDING" });
  return toApprovalRecord(input.id)!;
}

export function readApproval(id: string): ApprovalRecord | null {
  return toApprovalRecord(id);
}

export function resolveApproval(
  id: string,
  resolution: ApprovalResolutionFile
): ApprovalRecord | null {
  const dir = join(getApprovalsRoot(), id);
  ensureDir(dir);
  writeJsonAtomic(join(dir, "resolution.json"), resolution);
  return toApprovalRecord(id);
}

export function listApprovals(options: {
  projectId?: string;
  status?: ApprovalStatus | "ALL";
  limit?: number;
} = {}): ApprovalRecord[] {
  const dirs = listChildDirectories(getApprovalsRoot());
  const limit = options.limit ?? 100;
  const records: ApprovalRecord[] = [];
  for (const id of dirs) {
    const record = toApprovalRecord(id);
    if (!record) continue;
    if (options.projectId && record.project_id !== options.projectId) continue;
    if (options.status && options.status !== "ALL" && record.status !== options.status) continue;
    records.push(record);
    if (records.length >= limit) break;
  }
  return records;
}
