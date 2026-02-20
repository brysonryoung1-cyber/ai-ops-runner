/**
 * Strict allowlist of remote operations.
 * Each entry maps an action name to the exact remote command.
 * NO other commands may be executed — fail-closed.
 */

export type ActionName =
  | "doctor"
  | "llm_doctor"
  | "soma_connectors_status"
  | "soma_kajabi_bootstrap_start"
  | "soma_kajabi_bootstrap_status"
  | "soma_kajabi_bootstrap_finalize"
  | "soma_kajabi_gmail_connect_start"
  | "soma_kajabi_gmail_connect_status"
  | "soma_kajabi_gmail_connect_finalize"
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
  | "soma_kajabi_phase0"
  | "orb.backtest.bulk"
  | "orb.backtest.confirm_nt8"
  | "pred_markets.mirror.run"
  | "pred_markets.mirror.backfill"
  | "pred_markets.report.health"
  | "pred_markets.report.daily"
  | "autopilot_status"
  | "autopilot_enable"
  | "autopilot_disable"
  | "autopilot_run_now"
  | "autopilot_install";

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
  soma_connectors_status: {
    name: "soma_connectors_status",
    label: "Connectors Status",
    description: "Check Kajabi and Gmail connector readiness (no secrets)",
    remoteCommand: "cd /opt/ai-ops-runner && python3 -m services.soma_kajabi.connectors_status",
    timeoutSec: 15,
  },
  soma_kajabi_bootstrap_start: {
    name: "soma_kajabi_bootstrap_start",
    label: "Kajabi Bootstrap Start",
    description: "Start Kajabi connector bootstrap (instructions)",
    remoteCommand: "cd /opt/ai-ops-runner && python3 -m services.soma_kajabi.bootstrap kajabi start",
    timeoutSec: 30,
  },
  soma_kajabi_bootstrap_status: {
    name: "soma_kajabi_bootstrap_status",
    label: "Kajabi Bootstrap Status",
    description: "Check Kajabi bootstrap status",
    remoteCommand: "cd /opt/ai-ops-runner && python3 -m services.soma_kajabi.bootstrap kajabi status",
    timeoutSec: 10,
  },
  soma_kajabi_bootstrap_finalize: {
    name: "soma_kajabi_bootstrap_finalize",
    label: "Kajabi Bootstrap Finalize",
    description: "Finalize Kajabi storage_state setup",
    remoteCommand: "cd /opt/ai-ops-runner && python3 -m services.soma_kajabi.bootstrap kajabi finalize",
    timeoutSec: 30,
  },
  soma_kajabi_gmail_connect_status: {
    name: "soma_kajabi_gmail_connect_status",
    label: "Gmail Connect Status",
    description: "Check Gmail connector status",
    remoteCommand: "cd /opt/ai-ops-runner && python3 -m services.soma_kajabi.gmail_connect status",
    timeoutSec: 10,
  },
  soma_kajabi_gmail_connect_start: {
    name: "soma_kajabi_gmail_connect_start",
    label: "Gmail Connect Start",
    description: "Start Gmail OAuth/IMAP connector setup",
    remoteCommand: "cd /opt/ai-ops-runner && python3 -m services.soma_kajabi.gmail_connect start",
    timeoutSec: 30,
  },
  soma_kajabi_gmail_connect_finalize: {
    name: "soma_kajabi_gmail_connect_finalize",
    label: "Gmail Connect Finalize",
    description: "Finalize Gmail OAuth token storage",
    remoteCommand: "cd /opt/ai-ops-runner && python3 -m services.soma_kajabi.gmail_connect finalize",
    timeoutSec: 60,
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
  "orb.backtest.bulk": {
    name: "orb.backtest.bulk",
    label: "ORB Tier-1 Bulk Backtest",
    description:
      "Run Tier-1 bulk backtest (algo-nt8-orb). Writes to artifacts/backtests/<run_id>/tier1/. Requires OPENCLAW_ORB_REPO_PATH or /opt/algo-nt8-orb.",
    remoteCommand:
      "cd /opt/ai-ops-runner && ./ops/scripts/orb_backtest_bulk.sh",
    timeoutSec: 600,
  },
  "orb.backtest.confirm_nt8": {
    name: "orb.backtest.confirm_nt8",
    label: "ORB Tier-2 Confirm NT8",
    description:
      "Tier-2 confirmation stub: validate spec, write artifact skeleton, done.json with NT8_EXECUTOR_NOT_CONFIGURED (exit 3). Writes to artifacts/backtests/<run_id>/tier2/.",
    remoteCommand:
      "cd /opt/ai-ops-runner && ./ops/scripts/orb_backtest_confirm_nt8.sh",
    timeoutSec: 120,
  },
  "pred_markets.mirror.run": {
    name: "pred_markets.mirror.run",
    label: "Run Mirror (Phase 0)",
    description:
      "Read-only snapshot of Kalshi + Polymarket public markets into artifacts/pred_markets/<run_id>/.",
    remoteCommand:
      "cd /opt/ai-ops-runner && python3 -m services.pred_markets.run mirror_run",
    timeoutSec: 300,
  },
  "pred_markets.mirror.backfill": {
    name: "pred_markets.mirror.backfill",
    label: "Run Mirror Backfill",
    description: "Bounded backfill of market snapshots (Phase 0, no auth).",
    remoteCommand:
      "cd /opt/ai-ops-runner && python3 -m services.pred_markets.run mirror_backfill",
    timeoutSec: 600,
  },
  "pred_markets.report.health": {
    name: "pred_markets.report.health",
    label: "Run Health Report",
    description: "Check config + connector reachability; writes SUMMARY.md.",
    remoteCommand:
      "cd /opt/ai-ops-runner && python3 -m services.pred_markets.run report_health",
    timeoutSec: 60,
  },
  "pred_markets.report.daily": {
    name: "pred_markets.report.daily",
    label: "Run Daily Report",
    description: "Phase 0 daily report stub; writes SUMMARY.md.",
    remoteCommand:
      "cd /opt/ai-ops-runner && python3 -m services.pred_markets.run report_daily",
    timeoutSec: 60,
  },
  autopilot_status: {
    name: "autopilot_status",
    label: "Autopilot Status",
    description: "Read autopilot state (last_run.json). No side effects.",
    remoteCommand:
      'cat /var/lib/ai-ops-runner/autopilot/last_run.json 2>/dev/null || echo \'{"installed":false}\'',
    timeoutSec: 10,
  },
  autopilot_enable: {
    name: "autopilot_enable",
    label: "Enable Autopilot",
    description: "Create the enabled sentinel file so autopilot_tick deploys on next cycle.",
    remoteCommand:
      "mkdir -p /var/lib/ai-ops-runner/autopilot && touch /var/lib/ai-ops-runner/autopilot/enabled && echo '{\"ok\":true,\"enabled\":true}'",
    timeoutSec: 10,
  },
  autopilot_disable: {
    name: "autopilot_disable",
    label: "Disable Autopilot",
    description: "Remove the enabled sentinel file. Autopilot will skip on next cycle.",
    remoteCommand:
      "rm -f /var/lib/ai-ops-runner/autopilot/enabled && echo '{\"ok\":true,\"enabled\":false}'",
    timeoutSec: 10,
  },
  autopilot_run_now: {
    name: "autopilot_run_now",
    label: "Autopilot Run Now",
    description: "Trigger an immediate autopilot tick: fetch, deploy, verify (or rollback).",
    remoteCommand: "cd /opt/ai-ops-runner && ./ops/autopilot_tick.sh",
    timeoutSec: 900,
  },
  autopilot_install: {
    name: "autopilot_install",
    label: "Install Autopilot Timer",
    description: "Install or repair the openclaw-autopilot systemd timer on aiops-1.",
    remoteCommand:
      "cd /opt/ai-ops-runner && sudo ./ops/openclaw_install_autopilot.sh",
    timeoutSec: 30,
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
