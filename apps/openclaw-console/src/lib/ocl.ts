/**
 * OCL (OpenClaw Communication Layer) v1 — validator and normalizer.
 * Used by /api/ask and system.state_pack results.
 */

export interface OCLTaskRequest {
  action: string;
  params?: Record<string, unknown>;
  read_only?: boolean;
}

export interface OCLResult {
  status: "ok" | "fail" | "partial";
  checks: Array<{ name: string; pass: boolean; detail?: string }>;
  evidence: Array<{ path: string; label?: string }>;
  next?: OCLTaskRequest;
  run_id?: string;
  message?: string;
  artifact_dir?: string;
}

export interface AskResponse {
  answer: string;
  citations: string[];
  recommended_next_action: OCLTaskRequest;
  confidence: "LOW" | "MED" | "HIGH";
}

/** Validate OCL Result has required fields. */
export function validateOCLResult(result: unknown): result is OCLResult {
  if (!result || typeof result !== "object") return false;
  const r = result as Record<string, unknown>;
  if (r.status !== "ok" && r.status !== "fail" && r.status !== "partial") return false;
  if (!Array.isArray(r.checks)) return false;
  if (!Array.isArray(r.evidence)) return false;
  return true;
}

/** Normalize OCL TaskRequest: ensure required fields, default read_only. */
export function normalizeTaskRequest(req: unknown): OCLTaskRequest | null {
  if (!req || typeof req !== "object") return null;
  const r = req as Record<string, unknown>;
  const action = typeof r.action === "string" && r.action.length > 0 ? r.action : null;
  if (!action) return null;
  return {
    action,
    params: typeof r.params === "object" && r.params !== null ? (r.params as Record<string, unknown>) : undefined,
    read_only: r.read_only !== false,
  };
}

/** Validate AskResponse: citations must be non-empty. */
export function validateAskResponse(res: unknown): res is AskResponse {
  if (!res || typeof res !== "object") return false;
  const r = res as Record<string, unknown>;
  if (typeof r.answer !== "string") return false;
  if (!Array.isArray(r.citations)) return false;
  if (r.citations.length === 0) return false;
  const nextAction = normalizeTaskRequest(r.recommended_next_action);
  if (!nextAction) return false;
  const conf = r.confidence;
  if (conf !== "LOW" && conf !== "MED" && conf !== "HIGH") return false;
  return true;
}
