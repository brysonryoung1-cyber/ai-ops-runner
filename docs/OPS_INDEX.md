# OPS Index (Canonical)

Complete index of all operational scripts and commands in ai-ops-runner. This document is canonical and MUST be updated on every change.

**Last Updated**: 2026-02-15

## Quick Reference

| Command | Purpose |
|---------|---------|
| `./ops/openclaw_vps_deploy.sh` | **One-command full deploy to aiops-1** |
| `./ops/openclaw_heal.sh` | One-command apply + verify + evidence |
| `./ops/openclaw_doctor.sh` | Infrastructure health checks (9 checks) |
| `./ops/openclaw_guard.sh` | Continuous regression guard |
| `./ops/openclaw_notify.sh "msg"` | Send Pushover alert |
| `./ops/openclaw_apply_remote.sh` | Remote apply + verify from Mac |
| `./ops/ship_auto.sh` | Full ship autopilot (test → review → push) |

## Infrastructure (OpenClaw)

### Health & Verification

| Script | Description | Run As | Frequency |
|--------|-------------|--------|-----------|
| `ops/openclaw_doctor.sh` | Full health check (Tailscale, Docker, API, ports, disk, keys) | any | hourly (timer) |
| `ops/openclaw_heal.sh` | Apply + verify + evidence bundle | root | on-demand |
| `ops/openclaw_guard.sh` | Regression guard with safe auto-remediation | root | 10min (timer) |
| `ops/doctor_repo.sh` | Repo health (files, hooks, configs) | any | on-demand |

### Deploy

| Script | Description | Run As |
|--------|-------------|--------|
| `ops/openclaw_vps_deploy.sh` | Full 10-step deploy to aiops-1 (sync, build, heal, doctor, guard, console, tailscale serve, receipt) | any (SSHes as root) |
| `ops/openclaw_vps_deploy.sh --dry-run` | Print deploy plan without executing | any |

### Remediation

| Script | Description | Run As |
|--------|-------------|--------|
| `ops/openclaw_fix_ssh_tailscale_only.sh` | Lock sshd to Tailscale IP | root |
| `ops/openclaw_apply_remote.sh [host]` | Remote sync + build + fix + verify | any (SSHes as root) |
| `ops/openclaw_install_guard.sh` | Install/update guard systemd units | root |

### Notifications

| Script | Description |
|--------|-------------|
| `ops/openclaw_notify.sh "message"` | Send Pushover notification |
| `ops/openclaw_notify.sh --priority high "msg"` | High-priority alert |
| `ops/openclaw_notify.sh --dry-run "msg"` | Test without sending |
| `ops/openclaw_notify.sh --test` | Verify Pushover connectivity |

### Console

| Script | Description |
|--------|-------------|
| `ops/openclaw_console_build.sh` | Production build (npm ci + next build) |
| `ops/openclaw_console_start.sh` | Start production server (127.0.0.1:8787) |
| `ops/openclaw_console_stop.sh` | Graceful shutdown |
| `ops/openclaw_console_status.sh` | PID + URL + last log lines |
| `ops/openclaw_console_up.sh` | Development mode launcher |
| `ops/openclaw_console_install_macos_launchagent.sh` | macOS autostart |
| `ops/openclaw_console_token.py rotate` | Rotate auth token |
| `ops/openclaw_targets.py init` | Initialize target profiles |

### Key Management

| Command | Description |
|---------|-------------|
| `python3 ops/openai_key.py status` | Show masked key status |
| `python3 ops/openai_key.py doctor` | API smoke test |
| `python3 ops/openai_key.py set` | Store key to Keychain |
| `python3 ops/openai_key.py delete` | Remove from all backends |

## Review & Ship Pipeline

| Script | Description |
|--------|-------------|
| `ops/review_bundle.sh` | Generate bounded diff bundle |
| `ops/review_auto.sh` | One-command Codex review |
| `ops/review_finish.sh` | Advance baseline + push |
| `ops/ship_auto.sh` | Full autopilot (test → review → heal → push) |
| `ops/autoheal_codex.sh` | Auto-fix blockers from verdict |
| `ops/openclaw_codex_review.sh` | Automated diff-only review via OpenAI API |

## Runner & Jobs

| Script | Description |
|--------|-------------|
| `ops/runner_smoke.sh` | Docker smoke test (incl. ORB) |
| `ops/runner_submit_job.sh` | Submit a job |
| `ops/runner_submit_orb_review.sh` | ORB review bundle job |
| `ops/runner_submit_orb_doctor.sh` | ORB doctor job |
| `ops/runner_submit_orb_score.sh` | ORB score run job |

## VPS Deployment

| Script | Description |
|--------|-------------|
| `ops/vps_bootstrap.sh` | First-time VPS setup |
| `ops/vps_deploy.sh` | Full deploy (bootstrap + doctor) |
| `ops/vps_doctor.sh` | Remote VPS health check |
| `ops/vps_self_update.sh` | Review-gated self-update |

## Setup & Hooks

| Script | Description |
|--------|-------------|
| `ops/INSTALL_HOOKS.sh` | Install git hooks |
| `ops/ensure_openai_key.sh` | Source before Codex calls |

## Systemd Units

| Unit | Description | Frequency |
|------|-------------|-----------|
| `openclaw-guard.timer` | Regression guard | every 10 min |
| `openclaw-doctor.timer` | Health check | hourly |
| `openclaw-nightly.timer` | Nightly maintenance | daily |
| `ai-ops-runner.service` | Runner stack | always |
| `ai-ops-runner-health.timer` | Runner health | periodic |

## Tests

All tests run hermetically (no network, no real secrets).

| Test | Covers |
|------|--------|
| `ops/tests/openclaw_doctor_selftest.sh` | Doctor checks + tailnet-aware port audit |
| `ops/tests/openclaw_guard_selftest.sh` | Guard logic + safe remediation |
| `ops/tests/openclaw_fix_ssh_selftest.sh` | SSH fix + rollback |
| `ops/tests/openclaw_apply_remote_selftest.sh` | Remote apply + safety guards |
| `ops/tests/openclaw_install_guard_selftest.sh` | Guard systemd install |
| `ops/tests/openclaw_heal_selftest.sh` | Heal entrypoint + evidence |
| `ops/tests/openclaw_notify_selftest.sh` | Notifications + rate limiting |
| `ops/tests/openclaw_console_auth_selftest.sh` | Console auth + allowlist |
| `ops/tests/openclaw_codex_review_selftest.sh` | Automated review pipeline |
| `ops/tests/openclaw_vps_deploy_selftest.sh` | VPS deploy (mocked SSH, fail-closed) |
| `ops/tests/review_bundle_selftest.sh` | Review bundle generation |
| `ops/tests/review_auto_selftest.sh` | Review auto workflow |
| `ops/tests/review_finish_selftest.sh` | Baseline advance |
| `ops/tests/ship_auto_selftest.sh` | Ship autopilot |
| `ops/tests/orb_integration_selftest.sh` | ORB job integration |
| `ops/tests/openai_key_selftest.sh` | Key management |
| `ops/tests/test_openai_key.py` | Key management (Python) |
| `ops/tests/pre_push_gate_selftest.sh` | Push gate enforcement |

## Canonical Documents

| Document | Purpose |
|----------|---------|
| `docs/HANDOFF_CURRENT_STATE.md` | Living system state (canonical) |
| `docs/OPS_INDEX.md` | This file (canonical) |
| `docs/OPENCLAW_SECURITY_CONTRACT.md` | Non-negotiable security rules |
| `docs/OPENCLAW_TRANSFER_PACKET.md` | Handoff snapshot |
| `docs/OPENCLAW_NOTIFICATIONS.md` | Pushover alerting |
| `docs/OPENCLAW_CONSOLE.md` | Console operation + VPS deploy |
| `docs/OPENCLAW_HEAL.md` | Heal entrypoint contract |
| `docs/DEPLOY_VPS.md` | VPS deployment guide |
| `docs/OPENCLAW_LIVE_CHECKLIST.md` | "If it's live, these must be true" + verify commands |
| `docs/OPENCLAW_SUPPLY_CHAIN.md` | openclaw.ai supply-chain check (decision: NO) |
| `docs/REVIEW_WORKFLOW.md` | Review pipeline docs |
| `docs/CANONICAL_COMMANDS.md` | Quick command reference |
