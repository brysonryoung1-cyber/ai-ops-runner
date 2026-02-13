import { execFile } from "child_process";
import { getValidatedHost, getUser } from "./validate";
import { resolveAction } from "./allowlist";

export interface SSHResult {
  ok: boolean;
  action: string;
  stdout: string;
  stderr: string;
  exitCode: number | null;
  durationMs: number;
  error?: string;
}

/**
 * Execute an allowlisted SSH command against the validated AIOPS host.
 * Fail-closed: rejects unknown actions, non-Tailscale hosts, and missing SSH.
 */
export async function executeAction(actionName: string): Promise<SSHResult> {
  const start = Date.now();

  // 1. Validate action is in the allowlist
  const action = resolveAction(actionName);
  if (!action) {
    return {
      ok: false,
      action: actionName,
      stdout: "",
      stderr: "",
      exitCode: null,
      durationMs: Date.now() - start,
      error: `Action "${actionName}" is not in the allowlist. Refusing to execute.`,
    };
  }

  // 2. Validate host is a Tailscale IP
  let host: string;
  let user: string;
  try {
    host = getValidatedHost();
    user = getUser();
  } catch (err) {
    return {
      ok: false,
      action: actionName,
      stdout: "",
      stderr: "",
      exitCode: null,
      durationMs: Date.now() - start,
      error: err instanceof Error ? err.message : String(err),
    };
  }

  // 3. Execute via ssh — no shell interpolation, arguments passed directly
  const sshTarget = `${user}@${host}`;
  const sshArgs = [
    "-o", "ConnectTimeout=10",
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", "BatchMode=yes",
    sshTarget,
    action.remoteCommand,
  ];

  return new Promise<SSHResult>((resolve) => {
    const timeout = action.timeoutSec * 1000;
    const child = execFile(
      "ssh",
      sshArgs,
      { maxBuffer: 2 * 1024 * 1024, timeout },
      (err, stdout, stderr) => {
        const exitCode = child.exitCode;
        const durationMs = Date.now() - start;

        if (err && !stdout && !stderr) {
          resolve({
            ok: false,
            action: actionName,
            stdout: "",
            stderr: "",
            exitCode,
            durationMs,
            error: `SSH failed: ${err.message}. Is Tailscale up? Can you reach ${host}?`,
          });
          return;
        }

        resolve({
          ok: exitCode === 0,
          action: actionName,
          stdout: stdout || "",
          stderr: stderr || "",
          exitCode,
          durationMs,
        });
      }
    );
  });
}

/**
 * Quick connectivity check: ssh root@host 'echo ok'
 */
export async function checkConnectivity(): Promise<{
  ok: boolean;
  error?: string;
  durationMs: number;
}> {
  const start = Date.now();
  let host: string;
  let user: string;
  try {
    host = getValidatedHost();
    user = getUser();
  } catch (err) {
    return {
      ok: false,
      error: err instanceof Error ? err.message : String(err),
      durationMs: Date.now() - start,
    };
  }

  return new Promise((resolve) => {
    execFile(
      "ssh",
      [
        "-o", "ConnectTimeout=5",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "BatchMode=yes",
        `${user}@${host}`,
        "echo ok",
      ],
      { timeout: 10000 },
      (err, stdout) => {
        const durationMs = Date.now() - start;
        if (err || !stdout.trim().startsWith("ok")) {
          resolve({
            ok: false,
            error: `Cannot reach ${host} via SSH. Is Tailscale up? Error: ${err?.message || "no output"}`,
            durationMs,
          });
        } else {
          resolve({ ok: true, durationMs });
        }
      }
    );
  });
}
