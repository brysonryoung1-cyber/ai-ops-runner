"use client";

import { useState, useCallback } from "react";

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
 */
export function useExec() {
  const [loading, setLoading] = useState<string | null>(null);
  const [results, setResults] = useState<Record<string, ExecResult>>({});

  const exec = useCallback(async (action: string): Promise<ExecResult> => {
    setLoading(action);
    try {
      const res = await fetch("/api/exec", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
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
  }, []);

  const checkConnectivity = useCallback(async () => {
    try {
      const res = await fetch("/api/exec?check=connectivity");
      return await res.json();
    } catch {
      return { ok: false, error: "Network error" };
    }
  }, []);

  return { exec, loading, results, checkConnectivity };
}
