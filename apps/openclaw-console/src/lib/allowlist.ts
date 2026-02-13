/**
 * Strict allowlist of remote operations.
 * Each entry maps an action name to the exact remote command.
 * NO other commands may be executed — fail-closed.
 */

export type ActionName =
  | "doctor"
  | "apply"
  | "guard"
  | "ports"
  | "timer"
  | "journal"
  | "artifacts";

export interface AllowedAction {
  name: ActionName;
  label: string;
  description: string;
  remoteCommand: string;
  /** Expected maximum runtime in seconds (for UI timeout display) */
  timeoutSec: number;
}

export const ALLOWLIST: Record<ActionName, AllowedAction> = {
  doctor: {
    name: "doctor",
    label: "Run Doctor",
    description: "Run the OpenClaw doctor health check on aiops-1",
    remoteCommand:
      "cd /opt/ai-ops-runner && ./ops/openclaw_doctor.sh",
    timeoutSec: 30,
  },
  apply: {
    name: "apply",
    label: "Apply OpenClaw",
    description:
      "Sync repo, rebuild Docker stack, apply SSH fix, and verify on aiops-1",
    remoteCommand:
      "cd /opt/ai-ops-runner && ./ops/openclaw_apply_remote.sh",
    timeoutSec: 120,
  },
  guard: {
    name: "guard",
    label: "Install/Repair Guard",
    description:
      "Install or repair the openclaw-guard systemd timer on aiops-1",
    remoteCommand:
      "cd /opt/ai-ops-runner && sudo ./ops/openclaw_install_guard.sh",
    timeoutSec: 30,
  },
  ports: {
    name: "ports",
    label: "Show Port Audit",
    description: "List all listening TCP ports on aiops-1",
    remoteCommand: "ss -lntp",
    timeoutSec: 10,
  },
  timer: {
    name: "timer",
    label: "Guard Timer Status",
    description: "Show the status of the openclaw-guard systemd timer",
    remoteCommand:
      "systemctl status openclaw-guard.timer --no-pager",
    timeoutSec: 10,
  },
  journal: {
    name: "journal",
    label: "Guard Logs",
    description: "Tail the last 200 lines of openclaw-guard service logs",
    remoteCommand:
      "journalctl -u openclaw-guard.service -n 200 --no-pager",
    timeoutSec: 15,
  },
  artifacts: {
    name: "artifacts",
    label: "List Artifacts",
    description: "List latest artifact job directories with sizes",
    remoteCommand:
      'cd /opt/ai-ops-runner && ls -1dt artifacts/* 2>/dev/null | head -n 15 && echo "---" && du -sh artifacts/* 2>/dev/null | sort -h | tail -n 15',
    timeoutSec: 10,
  },
};

/**
 * Resolve an action name to its allowed action, or null if not in the allowlist.
 * Uses Object.hasOwn to reject prototype keys (__proto__, constructor, etc.).
 */
export function resolveAction(name: string): AllowedAction | null {
  if (Object.hasOwn(ALLOWLIST, name)) {
    return ALLOWLIST[name as ActionName];
  }
  return null;
}
