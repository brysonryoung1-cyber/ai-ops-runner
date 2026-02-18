"use client";

import { useState, useCallback } from "react";
import { useToken } from "./token-context";
import type { ForbiddenInfo } from "@/components/ForbiddenBanner";

export interface ExecResult {
  ok: boolean;
  action: string;
  stdout: string;
  stderr: string;
  exitCode: number | null;
  durationMs: number;
  error?: string;
  error_class?: string;
  httpStatus?: number;
}

/**
 * Authenticated fetch wrapper. Attaches X-OpenClaw-Token header and
 * detects 403 responses, surfacing structured ForbiddenInfo for the UI.
 */
export async function authedFetch(
  url: string,
  token: string,
  init?: RequestInit
): Promise<{ res: Response; forbidden?: ForbiddenInfo }> {
  const headers: Record<string, string> = {
    ...(init?.headers as Record<string, string> | undefined),
  };
  if (token) {
    headers["X-OpenClaw-Token"] = token;
  }
  const res = await fetch(url, { ...init, headers });

  if (res.status === 403) {
    let body: Record<string, unknown> = {};
    try {
      body = await res.clone().json();
    } catch {
      // non-JSON 403
    }
    const forbidden: ForbiddenInfo = {
      status: 403,
      route: url,
      error: typeof body.error === "string" ? body.error : undefined,
      error_class: typeof body.error_class === "string" ? body.error_class : undefined,
      reason: typeof body.reason === "string" ? body.reason : undefined,
      message: typeof body.message === "string" ? body.message : undefined,
      required_header: typeof body.required_header === "string" ? body.required_header : undefined,
      trust_tailscale: typeof body.trust_tailscale === "boolean" ? body.trust_tailscale : undefined,
      hq_token_required: typeof body.hq_token_required === "boolean" ? body.hq_token_required : undefined,
      admin_token_loaded: typeof body.admin_token_loaded === "boolean" ? body.admin_token_loaded : undefined,
      origin_seen: typeof body.origin_seen === "string" ? body.origin_seen : undefined,
      origin_allowed: typeof body.origin_allowed === "boolean" ? body.origin_allowed : undefined,
      timestamp: new Date().toISOString(),
    };
    return { res, forbidden };
  }

  return { res };
}

/**
 * Hook for executing allowlisted actions via the API.
 *
 * Automatically includes the X-OpenClaw-Token header from context.
 * Detects 403 responses and surfaces structured forbidden info.
 */
export function useExec() {
  const token = useToken();
  const [loading, setLoading] = useState<string | null>(null);
  const [results, setResults] = useState<Record<string, ExecResult>>({});
  const [lastForbidden, setLastForbidden] = useState<ForbiddenInfo | null>(null);

  const exec = useCallback(
    async (action: string): Promise<ExecResult> => {
      setLoading(action);
      try {
        const headers: Record<string, string> = {
          "Content-Type": "application/json",
        };
        if (token) {
          headers["X-OpenClaw-Token"] = token;
        }

        const res = await fetch("/api/exec", {
          method: "POST",
          headers,
          body: JSON.stringify({ action }),
        });

        if (res.status === 403) {
          let body: Record<string, unknown> = {};
          try {
            body = await res.clone().json();
          } catch {
            // non-JSON 403
          }
          setLastForbidden({
            status: 403,
            route: `/api/exec (action=${action})`,
            error: typeof body.error === "string" ? body.error : "Forbidden",
            error_class: typeof body.error_class === "string" ? body.error_class : undefined,
            reason: typeof body.reason === "string" ? body.reason : undefined,
            message: typeof body.message === "string" ? body.message : undefined,
            required_header: typeof body.required_header === "string" ? body.required_header : undefined,
            trust_tailscale: typeof body.trust_tailscale === "boolean" ? body.trust_tailscale : undefined,
            hq_token_required: typeof body.hq_token_required === "boolean" ? body.hq_token_required : undefined,
            admin_token_loaded: typeof body.admin_token_loaded === "boolean" ? body.admin_token_loaded : undefined,
            origin_seen: typeof body.origin_seen === "string" ? body.origin_seen : undefined,
            origin_allowed: typeof body.origin_allowed === "boolean" ? body.origin_allowed : undefined,
            timestamp: new Date().toISOString(),
          });
        }

        const data: ExecResult = await res.json();
        setResults((prev) => ({ ...prev, [action]: data }));
        return data;
      } catch (err) {
        const errResult: ExecResult = {
          ok: false,
          action,
          stdout: "",
          stderr: "",
          exitCode: null,
          durationMs: 0,
          error: `Network error: ${err instanceof Error ? err.message : String(err)}`,
        };
        setResults((prev) => ({ ...prev, [action]: errResult }));
        return errResult;
      } finally {
        setLoading(null);
      }
    },
    [token]
  );

  const checkConnectivity = useCallback(async () => {
    try {
      const headers: Record<string, string> = {};
      if (token) {
        headers["X-OpenClaw-Token"] = token;
      }
      const res = await fetch("/api/exec?check=connectivity", { headers });
      return await res.json();
    } catch {
      return { ok: false, error: "Network error" };
    }
  }, [token]);

  const dismissForbidden = useCallback(() => setLastForbidden(null), []);

  return { exec, loading, results, checkConnectivity, lastForbidden, dismissForbidden };
}
