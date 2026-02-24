// Auto-generated from config/action_registry.json — do not edit.
// Run: python3 ops/export_action_registry_ts.py

/** Map UI/API action name -> hostd action name. Single source: config/action_registry.json */
export const ACTION_TO_HOSTD: Record<string, string> = {
  "apply": "apply",
  "artifacts": "artifacts",
  "autopilot_disable": "autopilot_disable",
  "autopilot_enable": "autopilot_enable",
  "autopilot_install": "autopilot_install",
  "autopilot_run_now": "autopilot_run_now",
  "autopilot_status": "autopilot_status",
  "deploy_and_verify": "deploy_and_verify",
  "doctor": "doctor",
  "guard": "guard",
  "journal": "tail_guard_log",
  "llm.microgpt.canary": "llm.microgpt.canary",
  "llm_doctor": "llm_doctor",
  "orb.backtest.bulk": "orb.backtest.bulk",
  "orb.backtest.confirm_nt8": "orb.backtest.confirm_nt8",
  "port_audit": "port_audit",
  "ports": "port_audit",
  "pred_markets.mirror.backfill": "pred_markets.mirror.backfill",
  "pred_markets.mirror.run": "pred_markets.mirror.run",
  "pred_markets.report.daily": "pred_markets.report.daily",
  "pred_markets.report.health": "pred_markets.report.health",
  "sms_status": "sms_status",
  "soma_connectors_status": "soma_connectors_status",
  "soma_harvest": "soma_harvest",
  "soma_kajabi_auto_finish": "soma_kajabi_auto_finish",
  "soma_kajabi_bootstrap_finalize": "soma_kajabi_bootstrap_finalize",
  "soma_kajabi_bootstrap_start": "soma_kajabi_bootstrap_start",
  "soma_kajabi_bootstrap_status": "soma_kajabi_bootstrap_status",
  "soma_kajabi_capture_interactive": "soma_kajabi_capture_interactive",
  "soma_kajabi_discover": "soma_kajabi_discover",
  "soma_kajabi_gmail_connect_finalize": "soma_kajabi_gmail_connect_finalize",
  "soma_kajabi_gmail_connect_start": "soma_kajabi_gmail_connect_start",
  "soma_kajabi_gmail_connect_status": "soma_kajabi_gmail_connect_status",
  "soma_kajabi_phase0": "soma_kajabi_phase0",
  "soma_kajabi_session_check": "soma_kajabi_session_check",
  "soma_kajabi_snapshot_debug": "soma_kajabi_snapshot_debug",
  "soma_kajabi_unblock_and_run": "soma_kajabi_unblock_and_run",
  "soma_last_errors": "soma_last_errors",
  "soma_mirror": "soma_mirror",
  "soma_snapshot_home": "soma_snapshot_home",
  "soma_snapshot_practitioner": "soma_snapshot_practitioner",
  "soma_status": "soma_status",
  "soma_zane_finish_plan": "soma_zane_finish_plan",
  "tail_guard_log": "tail_guard_log",
  "timer": "timer",
};

/** Project ID -> set of allowlisted action ids for POST /api/projects/[projectId]/run */
export const PROJECT_ACTIONS: Record<string, ReadonlySet<string>> = {
  "soma_kajabi": new Set(["soma_connectors_status", "soma_kajabi_auto_finish", "soma_kajabi_bootstrap_finalize", "soma_kajabi_bootstrap_start", "soma_kajabi_bootstrap_status", "soma_kajabi_capture_interactive", "soma_kajabi_discover", "soma_kajabi_gmail_connect_finalize", "soma_kajabi_gmail_connect_start", "soma_kajabi_gmail_connect_status", "soma_kajabi_session_check", "soma_kajabi_snapshot_debug", "soma_kajabi_unblock_and_run", "soma_zane_finish_plan"]),
};

