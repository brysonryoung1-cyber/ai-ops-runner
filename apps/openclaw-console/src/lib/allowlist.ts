/**
 * Strict allowlist of remote operations.
 * Each entry maps an action name to the exact remote command.
 * NO other commands may be executed — fail-closed.
 */

export type ActionName =
  | "doctor"
  | "llm_doctor"
  | "apply"
  | "guard"
  | "ports"
  | "timer"
  | "journal"
  | "artifacts"
  | "deploy_and_verify"
  | "soma_snapshot_home"
  | "soma_snapshot_practitioner"
  | "soma_harvest"
  | "soma_mirror"
  | "soma_status"
  | "soma_last_errors"
  | "sms_status"
  | "soma_kajabi_phase0";

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
  llm_doctor: {
    name: "llm_doctor",
    label: "LLM Provider Doctor",
    description: "Preflight check OpenAI + Mistral; writes artifacts/doctor/providers/<run_id>/",
    remoteCommand:
      "cd /opt/ai-ops-runner && python3 -m src.llm.doctor",
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
  deploy_and_verify: {
    name: "deploy_and_verify",
    label: "Deploy+Verify",
    description:
      "Pull origin/main, rebuild, verify production, update project state; writes proof to artifacts/deploy/<run_id>/",
    remoteCommand: "cd /opt/ai-ops-runner && ./ops/deploy_pipeline.sh",
    timeoutSec: 600,
  },
  soma_snapshot_home: {
    name: "soma_snapshot_home",
    label: "Snapshot Home Library",
    description: "Take a Kajabi snapshot of the Home User Library",
    remoteCommand:
      'cd /opt/ai-ops-runner && python3 -m services.soma_kajabi_sync.snapshot --product "Home User Library"',
    timeoutSec: 120,
  },
  soma_snapshot_practitioner: {
    name: "soma_snapshot_practitioner",
    label: "Snapshot Practitioner Library",
    description: "Take a Kajabi snapshot of the Practitioner Library",
    remoteCommand:
      'cd /opt/ai-ops-runner && python3 -m services.soma_kajabi_sync.snapshot --product "Practitioner Library"',
    timeoutSec: 120,
  },
  soma_harvest: {
    name: "soma_harvest",
    label: "Harvest Gmail Videos",
    description: "Harvest video metadata from Zane's Gmail",
    remoteCommand:
      "cd /opt/ai-ops-runner && python3 -m services.soma_kajabi_sync.harvest",
    timeoutSec: 180,
  },
  soma_mirror: {
    name: "soma_mirror",
    label: "Mirror Home → Practitioner",
    description:
      "Diff Home vs Practitioner libraries and produce mirror report",
    remoteCommand:
      "cd /opt/ai-ops-runner && python3 -m services.soma_kajabi_sync.mirror --dry-run",
    timeoutSec: 60,
  },
  soma_status: {
    name: "soma_status",
    label: "Soma Status",
    description: "Show latest Soma artifact runs and health",
    remoteCommand:
      "cd /opt/ai-ops-runner && python3 -m services.soma_kajabi_sync.sms status",
    timeoutSec: 15,
  },
  soma_kajabi_phase0: {
    name: "soma_kajabi_phase0",
    label: "Soma Kajabi Phase 0",
    description:
      "Read-only: Kajabi snapshot + Gmail harvest (Zane McCourtney, has:attachment) + video_manifest.csv",
    remoteCommand:
      "cd /opt/ai-ops-runner && python3 -m services.soma_kajabi.phase0_runner",
    timeoutSec: 300,
  },
  soma_last_errors: {
    name: "soma_last_errors",
    label: "Soma Last Errors",
    description: "Show the last 5 Soma/SMS error messages",
    remoteCommand:
      'cd /opt/ai-ops-runner && python3 -c "from services.soma_kajabi_sync.sms import get_last_errors; errs=get_last_errors(5); print(chr(10).join(f\\"{e[\'timestamp\'][:16]}: {e[\'message\']}\\\" for e in errs) if errs else \'No recent errors.\')"',
    timeoutSec: 10,
  },
  sms_status: {
    name: "sms_status",
    label: "SMS Status",
    description: "Test SMS (Twilio) configuration and connectivity",
    remoteCommand:
      "cd /opt/ai-ops-runner && python3 -m services.soma_kajabi_sync.sms test",
    timeoutSec: 15,
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
