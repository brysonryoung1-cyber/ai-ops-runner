"use client";

import { useState, useCallback } from "react";
import { useToken } from "./token-context";

export interface ExecResult {
  ok: boolean;
  action: string;
  stdout: string;
  stderr: string;
  exitCode: number | null;
  durationMs: number;
  error?: string;
}

/**
 * Hook for executing allowlisted actions via the API.
 *
 * Automatically includes the X-OpenClaw-Token header from context.
 */
export function useExec() {
  const token = useToken();
  const [loading, setLoading] = useState<string | null>(null);
  const [results, setResults] = useState<Record<string, ExecResult>>({});

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

  return { exec, loading, results, checkConnectivity };
}
