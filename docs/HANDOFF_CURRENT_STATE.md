# Handoff — Current State

## Last Updated

2026-02-15 (Soma Kajabi + SMS milestone)

## Status

**LIVE on aiops-1.** Production-grade OpenClaw control plane fully deployed and phone-accessible via Tailscale Serve.

- **One-command deploy**: `ops/openclaw_vps_deploy.sh` — 10-step scripted deploy (sync, build, heal, doctor, guard, console, tailscale serve, receipt)
- **Phone access**: `https://aiops-1.tailc75c62.ts.net` via Tailscale Serve (HTTPS 443 → 127.0.0.1:8787)
- **Canonical docs**: HANDOFF + OPS_INDEX as single source of truth; security contract, transfer packet, notification/heal/console docs
- **Hardened console**: allowlist-only with schema validation, token auth, action lock, audit log, CSRF protection, VPS deploy behind Tailscale
- **Soma Kajabi Library Ownership**: First-class workflow for Kajabi snapshots (Home + Practitioner), Gmail video harvest, and library mirroring. CLI entrypoints + console UX + SMS commands.
- **SMS integration**: Twilio-based outbound alerts (workflow SUCCESS/FAIL, doctor FAIL, guard FAIL, nightly FAIL, SIZE_CAP WARN) + inbound commands (STATUS, RUN_SNAPSHOT, RUN_HARVEST, RUN_MIRROR, LAST_ERRORS). Behind allowlist + token, tailnet-only.
- **Outbound notifications**: Pushover-first + SMS; rate-limited alerts from guard/doctor/deploy/nightly/SIZE_CAP
- **One-command heal**: `ops/openclaw_heal.sh` — apply + verify + evidence bundle
- **Expanded doctor**: 9 checks — Docker port audit, disk pressure, log growth, key health (fingerprint), console bind, guard timer health, JSON output
- **Automated review**: `ops/openclaw_codex_review.sh` — OpenAI API diff-only review with security gates
- **Supply-chain decision**: openclaw.ai is open-source (MIT) but NOT adopted for ops — see `docs/OPENCLAW_SUPPLY_CHAIN.md`

Docker smoke test passing. Full ops/review/ship framework active. ORB integration jobs implemented and tested. VPS deployment automation (private-only, Tailscale-only). openclaw_doctor uses tailnet-aware port audit. OpenClaw safety permanently locked: continuous VPS guard (10 min) with safe auto-remediation. **Soma Kajabi Library Ownership** workflow active with CLI + console + SMS. See `docs/SOMA_KAJABI_RUNBOOK.md`.

## OpenClaw Current State

### Infrastructure Components

| Component | Status | Location |
|-----------|--------|----------|
| **VPS Deploy** | One-command (10 steps) | `ops/openclaw_vps_deploy.sh` |
| Doctor | Active (hourly timer, 9 checks) | `ops/openclaw_doctor.sh` |
| Guard | Active (10-min timer) | `ops/openclaw_guard.sh` |
| SSH Hardening | Locked (Tailscale-only) | `ops/openclaw_fix_ssh_tailscale_only.sh` |
| Console | Production (127.0.0.1:8787) | `apps/openclaw-console/` |
| **Phone Access** | Tailscale Serve HTTPS | `https://aiops-1.tailc75c62.ts.net` |
| Notifications | Pushover + SMS (outbound-only) | `ops/openclaw_notify.sh`, `ops/openclaw_notify_sms.sh` |
| Heal | One-command entrypoint | `ops/openclaw_heal.sh` |
| **Soma Kajabi Sync** | Active (on-demand) | `services/soma_kajabi_sync/` |
| **SMS Commands** | Active (Twilio) | `ops/openclaw_sms.sh` |
| Artifacts | `./artifacts/<job_id>/` | Per-job output directories |
| Soma Artifacts | `./artifacts/soma/<run_id>/` | Snapshots, manifests, reports |

### Doctor Checks

1. Tailscale connectivity
2. Docker Compose stack health
3. API healthz (127.0.0.1:8000)
4. Public port audit (tailnet-aware)
5. Docker published ports audit
6. Disk pressure + log growth
7. Key health (OpenAI masked fingerprint, Pushover)
8. Console bind verification
9. Guard timer health (active + recent PASS/FAIL entries)

### Guard Behavior

- Runs every 10 minutes via `openclaw-guard.timer`
- If doctor PASS → log + exit
- If FAIL + Tailscale up + sshd public → auto-remediate
- If Tailscale down → NEVER touch sshd (lockout prevention)
- Sends Pushover alert on first failure (rate-limited per 30 min)
- Sends emergency alert on remediation failure

## Operating Rules (Canonical)

These rules are **non-negotiable**. All code and automation MUST comply.

1. **Single source of truth**: `docs/HANDOFF_CURRENT_STATE.md` + `docs/OPS_INDEX.md` are canonical. Updated on every change.

2. **Automation-first**: Every recurring action becomes a script in `ops/`. Every script includes a hermetic self-test (mocked; no network; no real secrets).

3. **Fail-closed security**: Only allowed listener CIDRs are 127.0.0.0/8, ::1, and 100.64.0.0/10 (Tailscale). Everything else is FAIL. Doctor + Guard enforce and alert on regressions.

4. **Console allowlist-only**: No arbitrary shell execution. Commands must be registered in `src/lib/allowlist.ts`. Minimal auth gate (token) even for localhost. CSRF protection. Action lock prevents overlapping execution.

5. **Key handling contract**: Resolution order: env → macOS Keychain → Linux file. Keys NEVER printed to human output. Doctor verifies presence without printing secrets (masked fingerprint + last-success only).

6. **No interactive prompts**: Anywhere in runtime or ops paths. All secret entry via dedicated `set` subcommands.

7. **Outbound-only notifications**: Pushover HTTPS POST only. No inbound webhooks. No callback URLs. Rate-limited.

8. **Review-gated updates**: VPS only deploys APPROVED code. Push gate validates non-simulated verdicts with Codex CLI provenance.

## Out of Scope

The following are **out of scope** for this repository:
- **NT8 / NinjaTrader** strategy code, deployment, and Windows VPS mechanics
- **ORB C# strategy internals** — ORB is permitted ONLY as a runner use-case (review bundle generation, log auditing, artifacts)
- **Windows VPS** configuration and management
- **openclaw.ai assistant** — NOT installed on the ops plane. See `docs/OPENCLAW_SUPPLY_CHAIN.md` for rationale. "OpenClaw" in this project = private ops runner + doctor/guard + console only.

## Recent Changes

- **Soma Kajabi Library Ownership + SMS** (2026-02-15):
  - **New service**: `services/soma_kajabi_sync/` — Python service with CLI entrypoints for Kajabi snapshots, Gmail video harvest, and library mirroring
  - **Snapshot CLI**: `python3 -m services.soma_kajabi_sync.snapshot --product "Home User Library"` → `artifacts/soma/<run_id>/snapshot.json`
  - **Harvest CLI**: `python3 -m services.soma_kajabi_sync.harvest` → `artifacts/soma/<run_id>/gmail_video_index.json` + `video_manifest.csv`
  - **Mirror CLI**: `python3 -m services.soma_kajabi_sync.mirror` → `artifacts/soma/<run_id>/mirror_report.json` + `changelog.md`
  - **SMS integration**: Twilio-based outbound alerts + inbound commands (STATUS, RUN_SNAPSHOT, RUN_HARVEST, RUN_MIRROR, LAST_ERRORS)
  - **SMS security**: Behind allowlist + token, tailnet-only, rate-limited (1/min inbound, 30min outbound), fail-closed
  - **Console "Soma" page**: New sidebar section with action buttons for all Soma workflows, status cards, artifact viewer
  - **Allowlist expanded**: 6 new console actions (soma_snapshot_home, soma_snapshot_practitioner, soma_harvest, soma_mirror, soma_status, sms_status)
  - **SMS webhook**: `/api/sms` route for Twilio inbound, with TwiML responses
  - **Soma smoke test**: `ops/soma_smoke.sh` — verifies all modules, artifact writing, integrity checks (no credentials needed)
  - **Apply remote updated**: Step 5 now includes Soma smoke test
  - **Doctor SMS alerts**: SMS notifications sent on doctor/guard/nightly failures (alongside Pushover)
  - **Docker overlay**: `docker-compose.soma.yml` for containerized Soma runs
  - **Configs updated**: `policies.yaml` (soma + sms lanes), `projects.yaml` (soma-kajabi project), `job_allowlist.yaml` (5 new jobs)
  - **Hermetic tests**: 4 test files — test_artifacts.py, test_config.py, test_sms.py, test_mirror.py
  - **Ops selftests**: soma_smoke_selftest.sh, openclaw_sms_selftest.sh
  - **Docs**: `docs/SOMA_KAJABI_RUNBOOK.md` — complete runbook with setup, operations, troubleshooting
  - **Secrets**: KAJABI_SESSION_TOKEN, GMAIL_USER, GMAIL_APP_PASSWORD, TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER, SMS_ALLOWLIST — all via standard resolution (env → Keychain → file)

- **OpenClaw LIVE deploy + phone access** (2026-02-15):
  - **One-command deploy**: `ops/openclaw_vps_deploy.sh` — 10-step automated deploy to aiops-1 over Tailscale (sync, docker build, heal, doctor, guard install, console build, bind verify, tailscale serve, phone URL validate, deploy receipt)
  - **Phone access**: Console exposed via Tailscale Serve at `https://aiops-1.tailc75c62.ts.net` (HTTPS 443 → http://127.0.0.1:8787)
  - **Deploy receipt artifacts**: JSON + text receipts in `artifacts/deploy/<timestamp>/` with timestamps, key status, doctor summary (no secrets)
  - **Notifications expanded**: Deploy PASS/FAIL alerts, nightly job FAIL alerts, SIZE_CAP WARN alerts. All rate-limited per check_id per 30 min.
  - **Console schema validation**: Strict JSON body validation on exec API — rejects unexpected fields, validates action type
  - **Doctor expanded to 9 checks**: Added guard timer health check (active + recent PASS/FAIL entries), enhanced key fingerprint display
  - **Supply-chain verification**: openclaw.ai confirmed open-source (MIT, 196k stars) but NOT adopted — attack surface too broad for ops plane. Decision documented in `docs/OPENCLAW_SUPPLY_CHAIN.md`.
  - **Live checklist**: `docs/OPENCLAW_LIVE_CHECKLIST.md` — single-page verification with commands
  - **Hermetic tests**: 29 new assertions for VPS deploy selftest (mocked SSH, fail-closed, security patterns)
  - **Explicit boundary**: openclaw.ai assistant is NOT installed on the ops plane. "OpenClaw" = private ops runner + doctor/guard + console only.
- **OpenClaw production-grade control plane upgrade** — Major hardening across all components:
  - **Canonical docs**: Created `OPENCLAW_TRANSFER_PACKET.md`, `OPENCLAW_SECURITY_CONTRACT.md`, `OPENCLAW_NOTIFICATIONS.md`, `OPENCLAW_HEAL.md`, `OPS_INDEX.md` (canonical). Updated `HANDOFF_CURRENT_STATE.md` with Operating Rules + Out of Scope.
  - **Notifications (Pushover)**: `ops/openclaw_notify.sh` — outbound-only alerts with rate limiting (30min per check_id), priority levels, dry-run mode. Secrets via key handling contract. Integrated into guard (FAIL → alert, remediation failure → emergency) and doctor (optional notify mode).
  - **Heal entrypoint**: `ops/openclaw_heal.sh` — idempotent Apply + Verify + Evidence. Private-posture pre-check, optional fix application (lockout-safe), doctor verification, evidence bundle to `artifacts/evidence/<timestamp>_<host>/` with listeners, sshd config, guard status, docker ports, summary.json. `--check-only`, `--verify-only`, `--notify` modes.
  - **Console hardening**: Action lock (prevents overlapping execution, stale lock cleanup at 5min), append-only audit log (`data/audit.jsonl` with actor fingerprint, never raw token), payload size limit (1MB), session TTL via token rotation, Tailscale HTTPS origin support for phone access.
  - **Doctor expansion**: 8 checks now (was 4). New: Docker published ports audit (validates binds within allowed CIDRs), disk pressure (configurable warn/fail thresholds), log growth caps, OpenAI key presence + optional smoke test, Pushover key presence, console bind verification. JSON output to `artifacts/doctor/<timestamp>/doctor.json`.
  - **Automated review**: `ops/openclaw_codex_review.sh` — submits diff bundle to OpenAI API (chat completions), gets structured verdict with 5 security gates (public_binds, allowlist_bypass, key_handling, guard_doctor_intact, lockout_risk). `--gate` mode exits nonzero on BLOCKED. Stores verdict in `artifacts/codex_review/`.
  - **VPS console deploy**: `docker-compose.console.yml` + `Dockerfile` for console. Bind to 127.0.0.1:8787 only. Expose via `tailscale serve --bg --https=443`. Documented in `OPENCLAW_CONSOLE.md`.
  - **Tests**: 68 new test assertions across 4 selftest files:
    - `openclaw_notify_selftest.sh` (14 tests): dry-run, rate limiting, mock curl, secret safety
    - `openclaw_heal_selftest.sh` (17 tests): structure, test mode, doctor pass/fail paths
    - `openclaw_console_auth_selftest.sh` (21 tests): middleware, allowlist, audit, action lock, bind safety
    - `openclaw_codex_review_selftest.sh` (16 tests): security gates, API usage, structure
- **OpenClaw Console — production-grade operator panel** — Full lifecycle management for the console:
  - **Production build/start/stop/status** — `ops/openclaw_console_build.sh` (npm ci + next build), `ops/openclaw_console_start.sh` (production server, 127.0.0.1 only, PID file, timestamped logs), `ops/openclaw_console_stop.sh` (graceful shutdown + force-kill fallback), `ops/openclaw_console_status.sh` (PID, URL, last 30 log lines). All idempotent.
  - **macOS LaunchAgent autostart** — `ops/openclaw_console_install_macos_launchagent.sh` installs `com.openclaw.console.plist` for RunAtLoad. Uninstall script included. Idempotent.
  - **Keychain auth token** — `ops/openclaw_console_token.py` (status/rotate) stores a 256-bit hex token in macOS Keychain (service=`ai-ops-runner`, account=`OPENCLAW_CONSOLE_TOKEN`). `start.sh` reads it from Keychain and passes as env var. Next.js middleware enforces `X-OpenClaw-Token` header on all `/api/*` routes. 401 on mismatch; single-line security event logged (no secrets).
  - **Tailscale-only target profiles** — `ops/openclaw_targets.py` (init/show/set-active) manages `~/.config/openclaw/targets.json`. Hosts must be in `100.64.0.0/10`; non-tailnet IPs rejected fail-closed. Console UI shows active target in sidebar.
  - **Enhanced UX** — Overview page now shows guard timer status + last 20 guard log lines. Actions page adds "Tail Guard Log" button. Dynamic port support via `OPENCLAW_CONSOLE_PORT` env var.
  - **Security hardening** — Token auth middleware (fail-closed), dynamic origin validation (CSRF), no permissive CORS headers, command allowlist preserved, apply failures not masked.
  - **Docs** — `docs/OPENCLAW_CONSOLE_RUNBOOK.md` with all commands + troubleshooting.
  - **Tests** — `test_openclaw_console_token.py` (9 tests: generation, masking), `test_openclaw_targets.py` (20 tests: IP validation, target schema, boundary checks).
- **Review gate OpenAI Keychain hardening** — Non-interactive, deterministic key loading:
  - **Canonical Keychain convention**: service=`ai-ops-runner`, account=`OPENAI_API_KEY` (migrates legacy entries automatically)
  - **New public API**: `load_openai_api_key() -> str`, `load_openai_api_key_masked() -> str`, `assert_openai_api_key_valid() -> None`
  - **Package import**: `from ops.openai_key import load_openai_api_key` (added `ops/__init__.py`)
  - **CLI enhanced**: `python3 ops/openai_key.py doctor` runs OpenAI API smoke test; `status` shows source (env/keychain)
  - **Non-interactive resolve**: resolution chain (env → Keychain → Linux file) NEVER prompts interactively; `set` subcommand handles key entry
  - **Review gate audit**: `review_auto.sh` prints masked key fingerprint after loading
  - No manual key pasting required if Keychain is set; use `python3 ops/openai_key.py doctor` to validate.
- **OpenClaw Console** — Private, macOS-style web UI for managing OpenClaw on aiops-1:
  - **`apps/openclaw-console/`** — Next.js app (TypeScript + Tailwind CSS) bound to `127.0.0.1:8787` only
  - **Sidebar nav + 4 pages**: Overview (doctor/ports/timer/docker status), Logs (guard journal), Artifacts (job dirs), Actions (run doctor/apply/guard/ports)
  - **Strict SSH allowlist** — Only 7 predefined commands can be executed: doctor, apply, guard, ports, timer, journal, artifacts. No arbitrary execution.
  - **Tailscale CGNAT validation** — `AIOPS_HOST` must be in `100.64.0.0/10`; anything else is rejected fail-closed
  - **SSH connectivity check** — Probes `ssh root@<HOST> 'echo ok'` on page load; shows clear error if unreachable
  - **Parsed + raw output** — Status cards with PASS/FAIL indicators + collapsible raw terminal output
  - **Launcher**: `./ops/openclaw_console_up.sh` — ensures deps, creates `.env.local`, starts on port 8787
  - **Docs**: `docs/OPENCLAW_CONSOLE.md` — quick start, security model, troubleshooting
- **OpenClaw safety permanently locked** — Two new automation layers ensure OpenClaw security cannot regress:
  - **One-command apply+verify** — `ops/openclaw_apply_remote.sh [host]` SSHes to aiops-1 (default `root@100.123.61.57`), syncs to origin/main, rebuilds Docker stack, applies SSH Tailscale-only fix, runs full doctor, and prints port proof. Exits nonzero if doctor fails. Tailscale-down guard: skips SSH fix if Tailscale is not up (avoids lockout).
  - **Continuous VPS guard** — `openclaw-guard.timer` runs every 10 minutes via systemd:
    - Runs `openclaw_doctor.sh`. If PASS → logs and exits 0.
    - If FAIL: checks Tailscale IPv4 availability AND whether sshd is bound to a public address.
    - If BOTH conditions met → runs `openclaw_fix_ssh_tailscale_only.sh`, then re-runs doctor.
    - **CRITICAL SAFETY**: If Tailscale is NOT up, NEVER touches sshd config (prevents bricking remote access).
    - Always writes timestamped report to `/var/log/openclaw_guard.log` (append).
  - **Guard install** — `ops/openclaw_install_guard.sh` copies systemd units, reloads, enables timer. Idempotent. Test-mode support via `OPENCLAW_GUARD_INSTALL_ROOT`.
  - **Hermetic tests** — 62 new test assertions across 3 selftest files:
    - `openclaw_apply_remote_selftest.sh` (18 tests): static structure, safety guards, Tailscale-down skip
    - `openclaw_guard_selftest.sh` (25 tests): doctor pass/fail, Tailscale-down skip, remediation success/failure, post-remediation re-check, log append, IPv6 detection
    - `openclaw_install_guard_selftest.sh` (19 tests): unit copy, permissions, systemctl commands, idempotency, content integrity
  - **One-command deploy from LOCAL**:
    ```bash
    ./ops/openclaw_apply_remote.sh
    ```
  - **Guard status check on VPS**:
    ```bash
    systemctl status openclaw-guard.timer
    cat /var/log/openclaw_guard.log | tail -20
    ```
- **SSH remediation deterministic on Ubuntu** — Three root causes of sshd still binding `0.0.0.0:22` / `[::]:22` after fix have been eliminated:
  - **systemd daemon-reload** — After masking socket units (`ssh.socket`, `sshd.socket`), `systemctl daemon-reload` is now called to force systemd to pick up the mask immediately. Without this, systemd used cached state and the socket mask didn't take effect before service restart. Socket death is verified; force-killed if still active.
  - **Include directive enforcement** — Script now verifies that `sshd_config` contains `Include /etc/ssh/sshd_config.d/*.conf`. If missing, it prepends one (with backup). Without this, the `99-tailscale-only.conf` drop-in was completely ignored by sshd.
  - **Effective config validation** — After writing the drop-in and before restart, the script validates `sshd -T` effective output to confirm `listenaddress` contains ONLY the Tailscale IP and `addressfamily` is `inet`. Rolls back if unexpected values detected.
  - **Backup guard** — Conflict scan no longer overwrites the Include-check backup, ensuring rollback always restores the truly original `sshd_config`.
  - **Increased post-restart settle time** — Sleep increased from 1s to 2s for reliability on slower VPS instances.
  - **Selftest expanded** — 5 new tests (16–20): daemon-reload ordering, Include directive add/preserve, effective config validation rollback, IPv6 `[::]:22` detection.
  - **Doctor selftest expanded** — 3 new tests (21–23): dual-stack public sshd, sshd-on-loopback pass, mixed tailnet+public.
  - **VPS verify commands** (run on aiops-1 after pull):
    ```bash
    cd /opt/ai-ops-runner && git pull --ff-only
    sudo ./ops/openclaw_fix_ssh_tailscale_only.sh
    ./ops/openclaw_doctor.sh
    ss -lntp | grep ':22 '
    ```
- **SSH remediation + OpenAI key hardening** — Two major improvements:
  - **sshd Tailscale-only fix hardened** — `openclaw_fix_ssh_tailscale_only.sh` now:
    - Detects and disables/masks ALL socket-activation units: `ssh.socket`, `sshd.socket`, and templated `ssh@*.socket`.
    - Detects whether the active daemon unit is `ssh.service` or `sshd.service` and restarts the correct one.
    - Scans `/etc/ssh/sshd_config` and `/etc/ssh/sshd_config.d/*.conf` for conflicting `ListenAddress` and `AddressFamily` directives, comments them out with timestamped backups.
    - Validates with `sshd -t`, `sshd -T`, and `ss -lntp`; fail-closed if any public bind remains.
    - Safe rollback: on validation failure, restores backups and restarts service with original config.
    - Selftest expanded to 41 assertions (was 17): conflicting ListenAddress, sshd.socket, ssh@.socket, rollback, AddressFamily inet6, sshd.service detection.
  - **OpenAI key management hardened** — `ops/openai_key.py` now:
    - NEVER prints the raw key to human-visible output. Default mode shows masked status (`sk-…abcd`).
    - Importable public API: `get_openai_api_key()`, `set_openai_api_key(key)`, `delete_openai_api_key()`, `openai_key_status(masked=True)`.
    - CLI subcommands: `set` (interactive prompt), `delete` (remove from all backends), `status` (masked output).
    - `--emit-env` mode preserved for shell capture (refused on TTY, used by `ensure_openai_key.sh`).
    - `ensure_openai_key.sh` updated to use `--emit-env` instead of raw stdout capture.
    - 66 Python tests (was 31): mask function, public API, CLI subcommands, key-never-printed assertions.
    - Shell selftest updated: 18 assertions covering `--emit-env`, `status`, default mode, ensure wrapper.
  - **Doctor remediation guidance updated** — Matches the hardened fix script (mentions all socket units, conflicting directive scanning, rollback).
- **openclaw one-shot green: ssh.socket + loopback fixes** — Two root causes fixed:
  - **ssh.socket** — `openclaw_fix_ssh_tailscale_only.sh` now detects and disables systemd socket activation (`ssh.socket`), which was binding `:22` on `0.0.0.0`/`[::]` and ignoring `sshd_config` `ListenAddress` directives. Socket is disabled, stopped, and masked to prevent re-activation on upgrades.
  - **127.0.0.0/8 loopback** — `openclaw_doctor.sh` Python analyzer now classifies the entire `127.0.0.0/8` range as loopback (not just `127.0.0.1`). This correctly treats `systemd-resolve` on `127.0.0.53`/`127.0.0.54` as private.
  - **OPENCLAW_TEST_ROOT** — Fix script supports test mode via `OPENCLAW_TEST_ROOT` env var + stub binaries in PATH, enabling hermetic testing without root.
  - **Enhanced verification** — Fix script verification grep now catches `[::]:22` and `*:22` in addition to `0.0.0.0:22`. On failure, dumps `sshd -T`, `systemctl status`, and config file grep for debugging.
  - **New selftest** — `ops/tests/openclaw_fix_ssh_selftest.sh` (17 tests): ssh.socket detect/disable/mask, config write, verification pass/fail, non-tailnet IP rejection, sshd -t rollback.
  - **Extended selftest** — `ops/tests/openclaw_doctor_selftest.sh` expanded from 20 to 27 tests: added systemd-resolve on 127.0.0.53/54, full VPS scenario, public+resolve mixed, full 127.0.0.0/8 coverage, _is_loopback + ssh.socket static checks.
- **openclaw_doctor tailnet-aware port audit** — The Public Port Audit (check 4) now classifies ports correctly:
  - **Localhost** (127.0.0.1 / ::1) → always PASS
  - **Tailnet** (100.64.0.0/10) → treated as PRIVATE for any process (sshd, tailscaled, etc.)
  - **tailscaled** → allowed on any address (needed for DERP relay / WireGuard)
  - **sshd on 0.0.0.0 / :::** → FAIL with automated remediation instructions
  - **Any other process on public address** → FAIL
  - Remediation box printed when sshd is public-bound, pointing to `openclaw_fix_ssh_tailscale_only.sh`
  - Hermetic selftest: `ops/tests/openclaw_doctor_selftest.sh` (20 tests, including tailnet boundary checks)
- **Automated sshd remediation** — `ops/openclaw_fix_ssh_tailscale_only.sh`:
  - Detects Tailscale IPv4 via `tailscale ip -4`
  - Writes `/etc/ssh/sshd_config.d/99-tailscale-only.conf` (AddressFamily inet, ListenAddress <TS_IP>)
  - Does NOT change auth methods or disable root login (minimal, safe)
  - Validates with `sshd -t`, restarts sshd, verifies with `ss`
  - Fail-closed: exits non-zero if any step fails; removes drop-in on validation failure
- **orb_review_bundle success-with-warning** — SIZE_CAP fallback now exits 0 (not 6):
  - When SIZE_CAP is hit and fallback artifacts are generated successfully, the wrapper exits 0
  - Executor sets `status=success`, `exit_code=0` (previously `status=failure`, `exit_code=6`)
  - `artifact.json` carries warning flags: `size_cap_exceeded: true`, `warnings: ["SIZE_CAP_EXCEEDED"]`
  - Also includes `review_packets_archive` and `review_packets_dir` at top level
  - If fallback generation fails, wrapper still exits non-zero (fail-closed preserved)
  - Tests updated: FORCE_SIZE_CAP path now asserts exit 0 + all artifact flags
- **Secure OpenAI key loading** — Zero-recurring-step secret management for Codex CLI:
  - `ops/openai_key.py` — Cross-platform key loader (env → Keychain → Linux file → prompt)
  - `ops/ensure_openai_key.sh` — Shell wrapper (source before Codex calls)
  - macOS: Keychain storage uses `security` CLI with stdin piping (**no secret in argv** — eliminates credential-leak risk)
  - Linux: read from `/etc/ai-ops-runner/secrets/openai_api_key` (chmod 600)
  - Fail-closed: pipeline stops with clear message if key unavailable
  - Updated `review_auto.sh`, `autoheal_codex.sh`, `ship_auto.sh` to source the helper
  - `CODEX_SKIP=1` mode bypasses key loading (no Codex needed for simulated verdicts)
  - Selftests: `ops/tests/openai_key_selftest.sh` + `ops/tests/test_openai_key.py` (31 tests, including argv-leak guards)
- **ORB Wrapper Hardening** (see dedicated section below)
- **SIZE_CAP → review packets**: When `orb_review_bundle` hits exit code 6 (SIZE_CAP), the wrapper now auto-generates:
  - Per-file packet diffs in `review_packets/<stamp>/packet_NNN.txt`
  - `HOW_TO_PASTE.txt` with review instructions
  - `ORB_REVIEW_PACKETS.tar.gz` archive
  - `README_REVIEW_PACKETS.txt` guide
  - `size_cap_meta.json` (merged into `artifact.json` as `size_cap_fallback`)
- **Executor**: Reads `size_cap_meta.json` from artifact dir and includes `size_cap_fallback` field in `artifact.json`
- **Tests**: Added pytest tests for hooksPath config (clean-tree safe) and SIZE_CAP packet generation (8 new tests, including FORCE_SIZE_CAP end-to-end)
- **Selftest**: Extended `orb_integration_selftest.sh` with checks for hooksPath hardening, SIZE_CAP packet generation, FORCE_SIZE_CAP, and executor integration
- **VPS deployment**: Added private-only VPS deployment via Tailscale
  - `ops/vps_bootstrap.sh` — idempotent VPS setup (docker, tailscale, UFW, systemd)
  - `ops/vps_deploy.sh` — wrapper (bootstrap + doctor)
  - `ops/vps_doctor.sh` — remote health checks
  - `ops/vps_self_update.sh` — review-gated self-update (runs on VPS via systemd timer)
  - `docs/DEPLOY_VPS.md` — full deployment guide
- **Private-only networking**: docker-compose.yml hardened
  - Postgres/Redis: no published ports (internal docker network only)
  - API: bound to `127.0.0.1:8000` only (no public exposure)
  - Remote access via `tailscale serve` (HTTPS on tailnet)
- **Review-gated updates**: VPS self-update checks `LAST_REVIEWED_SHA.txt == origin/main HEAD`; fails closed if review gate not passed
- **Systemd timers**: auto-update every 15 min, daily smoke test at 06:00 UTC
- **ORB integration**: Added read-only analysis jobs for algo-nt8-orb (orb_review_bundle, orb_doctor, orb_score_run)
- **Repo allowlist**: New `configs/repo_allowlist.yaml` — runner rejects any repo URL not listed
- **Job allowlist**: Extended with 3 new ORB job types, each with `requires_repo_allowlist: true`
- **Executor**: Now writes `invariants` (read_only_ok, clean_tree_ok), `outputs`, `params` to artifact.json; MUTATION_DETECTED status on dirty worktree with changed file list
- **API**: Validates params against `allowed_params`; validates repo URL against repo allowlist for ORB jobs
- **Wrapper scripts**: `orb_wrappers/` contains per-job-type scripts that run inside the read-only worktree
- **CLI helpers**: `ops/runner_submit_orb_{review,doctor,score}.sh` — auto-resolve HEAD, poll, print artifacts
- **Smoke test**: `runner_smoke.sh` now includes ORB integration smoke (auto-resolves HEAD, graceful skip if offline)
- **Selftests**: `ops/tests/orb_integration_selftest.sh` validates configs, wrapper scripts, allowlist enforcement
- **Python tests**: `test_repo_allowlist.py` (10 tests), `test_orb_integration.py` (12 tests)
- **Doctor**: Checks for repo_allowlist.yaml, ORB wrapper scripts, and new CLI helpers

## Architecture

```
ops/
├── openai_key.py             # Secure OpenAI key manager (get/set/delete/status; never prints raw key)
├── ensure_openai_key.sh      # Shell wrapper — source before Codex calls
├── review_bundle.sh          # Generate bounded diff bundle (exit 6 = size cap → packet mode)
├── review_auto.sh            # One-command Codex review (writes meta provenance, npx fallback)
├── review_finish.sh          # Advance baseline + commit isolation (refuses simulated)
├── ship_auto.sh              # Full autopilot (test → review → heal → push, bounded)
├── autoheal_codex.sh         # Auto-fix blockers from verdict (allowlisted paths only)
├── doctor_repo.sh            # Verify repo health + hooks + ORB configs
├── INSTALL_HOOKS.sh          # Install git hooks idempotently
├── runner_smoke.sh           # Docker compose up + smoke test (incl. ORB integration)
├── runner_submit_job.sh      # Submit a specific job to the runner
├── runner_submit_orb_review.sh  # Submit orb_review_bundle + poll + print
├── runner_submit_orb_doctor.sh  # Submit orb_doctor + poll + print
├── runner_submit_orb_score.sh   # Submit orb_score_run + poll + print
├── openclaw_fix_ssh_tailscale_only.sh  # Lock sshd to Tailscale IP (root, fail-closed)
├── openclaw_apply_remote.sh  # One-command apply + verify from LOCAL to VPS
├── openclaw_guard.sh         # Continuous regression guard (systemd, safe auto-remediation, Pushover alerts)
├── openclaw_install_guard.sh # Install guard systemd units (idempotent)
├── openclaw_doctor.sh        # Infrastructure health (8 checks, JSON output, tailnet-aware)
├── openclaw_heal.sh          # One-command apply + verify + evidence bundle
├── openclaw_notify.sh        # Outbound Pushover notifications (rate-limited)
├── openclaw_codex_review.sh  # Automated diff-only review via OpenAI API
├── openclaw_vps_deploy.sh   # One-command full deploy to aiops-1 (10 steps)
├── openclaw_notify_sms.sh   # SMS alerts via Twilio (rate-limited, allowlist-only)
├── openclaw_sms.sh          # SMS CLI driver (send, alert, test, status)
├── soma_smoke.sh            # Soma workflow smoke test (no credentials needed)
├── vps_bootstrap.sh          # Idempotent VPS setup (docker, tailscale, UFW, systemd)
├── vps_deploy.sh             # Deploy wrapper (bootstrap + doctor)
├── vps_doctor.sh             # Remote VPS health check
├── vps_self_update.sh        # Review-gated self-update (runs on VPS)
├── schemas/
│   └── codex_review_verdict.schema.json
└── tests/
    ├── pre_push_gate_selftest.sh
    ├── review_bundle_selftest.sh
    ├── review_auto_selftest.sh
    ├── review_finish_selftest.sh
    ├── ship_auto_selftest.sh
    ├── orb_integration_selftest.sh
    ├── openclaw_doctor_selftest.sh
    ├── openclaw_fix_ssh_selftest.sh
    ├── openclaw_apply_remote_selftest.sh
    ├── openclaw_guard_selftest.sh
    ├── openclaw_install_guard_selftest.sh
    ├── openclaw_heal_selftest.sh
    ├── openclaw_notify_selftest.sh
    ├── openclaw_console_auth_selftest.sh
    ├── openclaw_codex_review_selftest.sh
    ├── openclaw_vps_deploy_selftest.sh
    ├── openai_key_selftest.sh
    ├── test_openai_key.py
    ├── soma_smoke_selftest.sh        # Soma workflow smoke selftest
    └── openclaw_sms_selftest.sh      # SMS integration selftest

configs/
├── job_allowlist.yaml        # Allowlisted job types (incl. ORB jobs)
└── repo_allowlist.yaml       # Allowlisted target repos (algo-nt8-orb)

apps/openclaw-console/              # Private OpenClaw management UI (production-grade)
├── Dockerfile                      # Multi-stage production Docker image
├── src/app/                        # Next.js App Router pages
│   ├── page.tsx                    # Overview (doctor, ports, timer, docker, guard logs)
│   ├── logs/page.tsx               # Guard journal tail
│   ├── artifacts/page.tsx          # Artifact directory listing
│   ├── actions/page.tsx            # Action buttons (doctor, apply, guard, ports, journal)
│   ├── soma/page.tsx              # Soma Kajabi Library page (snapshots, harvest, mirror)
│   ├── api/exec/route.ts          # API route (allowlisted SSH, audit log, action lock)
│   └── api/sms/route.ts           # SMS inbound webhook (Twilio → SSH bridge)
├── src/middleware.ts               # Token auth + payload limits (X-OpenClaw-Token)
├── src/lib/
│   ├── ssh.ts                      # SSH execution via child_process.execFile
│   ├── allowlist.ts                # Command allowlist (13 actions incl. Soma + SMS)
│   ├── validate.ts                 # Tailscale CGNAT + targets.json support
│   ├── audit.ts                    # Append-only audit log (JSONL, actor fingerprint)
│   ├── action-lock.ts              # Action lock (prevent overlapping execution)
│   ├── hooks.ts                    # React hooks with token auth
│   ├── token-context.tsx           # Client token context provider
│   └── target-context.tsx          # Client target info context
├── src/components/                 # Sidebar (with target badge), StatusCard, etc.
└── .env.example                    # AIOPS_HOST, AIOPS_USER

docs/
├── HANDOFF_CURRENT_STATE.md  # Canonical system state
├── OPS_INDEX.md              # Canonical ops command index
├── OPENCLAW_SECURITY_CONTRACT.md  # Non-negotiable security rules
├── OPENCLAW_TRANSFER_PACKET.md    # Handoff snapshot
├── OPENCLAW_NOTIFICATIONS.md # Pushover alerting
├── OPENCLAW_CONSOLE.md       # Console + VPS deploy + phone access
├── OPENCLAW_HEAL.md          # Heal entrypoint contract
├── OPENCLAW_ARCHITECTURE.md  # Architecture overview
├── DEPLOY_VPS.md             # VPS deployment guide
├── OPENCLAW_LIVE_CHECKLIST.md # "If it's live, these must be true" + commands
├── OPENCLAW_SUPPLY_CHAIN.md  # openclaw.ai supply-chain check (decision: NO)
├── SOMA_KAJABI_RUNBOOK.md   # Soma Kajabi Library ownership runbook
├── LAST_REVIEWED_SHA.txt
├── REVIEW_WORKFLOW.md
├── REVIEW_PACKET.md
└── CANONICAL_COMMANDS.md

docker-compose.console.yml   # Console Docker deploy (127.0.0.1:8787 only)
docker-compose.soma.yml      # Soma Kajabi Sync overlay (optional)

services/soma_kajabi_sync/         # Soma Kajabi Library Ownership workflow
├── __init__.py
├── config.py                      # Secret/config management (fail-closed)
├── artifacts.py                   # Artifact writing (snapshot, manifest, report, changelog)
├── snapshot.py                    # Kajabi snapshot CLI (API + Playwright fallback)
├── harvest.py                     # Gmail video harvest CLI (IMAP)
├── mirror.py                      # Library mirror CLI (Home → Practitioner)
├── sms.py                         # Twilio SMS (outbound alerts + inbound commands)
├── requirements.txt
├── Dockerfile
└── tests/
    ├── test_artifacts.py          # Artifact writing tests
    ├── test_config.py             # Config/secret tests
    ├── test_sms.py                # SMS allowlist/rate-limit tests
    └── test_mirror.py             # Mirror diff computation tests

services/test_runner/
├── orb_wrappers/             # Per-job-type scripts (read-only worktree safe)
│   ├── orb_review_bundle.sh
│   ├── orb_doctor.sh
│   └── orb_score_run.sh
└── test_runner/
    ├── repo_allowlist.py     # Repo allowlist enforcement
    └── (existing modules)
```

## ORB Wrapper Hardening

Two paper cuts have been eliminated to make ORB automation day-to-day useful:

### A) hooksPath — orb_doctor 18/18

**Problem**: ORB's `doctor_repo.sh` checks that `core.hooksPath` is set to `.githooks`. In the runner's ephemeral worktree (created from a bare mirror), this config was never set, causing a false finding (17/18).

**Fix (two layers)**:
1. **Executor** (`executor.py`, step 2a): After `create_worktree()` and **before** `make_readonly()`, the executor checks for `.githooks/` in the worktree and sets `git config core.hooksPath .githooks` with `check=True`. On failure, logs a warning with stderr (never logs success if it failed). This applies to *all* job types.
2. **Wrapper** (`orb_doctor.sh`): Belt-and-suspenders — the wrapper also sets `core.hooksPath` if `.githooks/` exists.

**Why it's safe**: `git config` writes to the gitdir config (located under `/repos/` outside the worktree), so no tracked files are modified, `git status --porcelain` stays clean, and mutation detection is NOT tripped.

**Verification**:
```bash
# In any runner job, after step 2a:
git -C "$WORKTREE" config core.hooksPath   # → .githooks
git -C "$WORKTREE" status --porcelain       # → (empty)
```

### B) SIZE_CAP → Review Packets (success-with-warning)

**Problem (original)**: When the review bundle exceeds the size cap, `orb_review_bundle` exited 6 → executor set `status=failure`, blocking automation even though fallback artifacts were generated.

**Fix (two phases)**:
1. **Phase 1** (previous): Auto-generate review packets on exit 6 (packets, archive, README, size_cap_meta.json).
2. **Phase 2** (current): After successful fallback generation, wrapper exits 0 → executor sets `status=success`.

**Artifact contract**: `artifact.json` now includes:
- `size_cap_exceeded: true` — top-level flag
- `warnings: ["SIZE_CAP_EXCEEDED"]` — structured warnings array
- `review_packets_archive: "ORB_REVIEW_PACKETS.tar.gz"` — path to archive
- `review_packets_dir: "review_packets/<stamp>"` — path to packet directory
- `size_cap_fallback: { ... }` — full metadata (preserved from phase 1)

**Fail-closed invariant**: If fallback generation fails (tar error, disk full, etc.), `set -euo pipefail` causes the wrapper to exit non-zero before reaching `exit 0`. Only a fully successful fallback reports success.

**Test-only flag**: `FORCE_SIZE_CAP=1` env var deterministically triggers the SIZE_CAP fallback path. Tests verify exit 0, all artifacts exist, and artifact.json contains warning flags.

**Verification**:
```bash
# Deterministic test (now exits 0):
FORCE_SIZE_CAP=1 ARTIFACT_DIR=/tmp/test SINCE_SHA=$(git rev-list --max-parents=0 HEAD) \
  bash services/test_runner/orb_wrappers/orb_review_bundle.sh
echo $?    # → 0
ls /tmp/test/  # → ORB_REVIEW_PACKETS.tar.gz, README_REVIEW_PACKETS.txt, size_cap_meta.json, ...
cat /tmp/test/size_cap_meta.json | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['size_cap_exceeded'], d['warnings'])"
# → True ['SIZE_CAP_EXCEEDED']
```

### C) openclaw_doctor tailnet-aware port audit

**Problem**: The Public Port Audit (check 4) used a simple grep that:
- Flagged tailscaled on tailnet IPs (100.64.0.0/10) as "public" — false positive
- Flagged sshd on 0.0.0.0:22 without remediation guidance
- Matched peer address column in `ss` output (0.0.0.0:*) — false positive for tailnet-bound processes

**Fix**: Replaced grep with an inline Python analyzer that:
1. Parses `ss -tlnp` output column-by-column (local addr:port in column 3)
2. Extracts process name from the `users:(("name",...))` field
3. Applies policy: localhost → OK, tailnet (100.64.0.0/10) → PRIVATE, tailscaled → allowed, public → FAIL
4. Prints structured output: `OK` | `VIOLATIONS` + lines + optional `SSHD_PUBLIC`
5. When `SSHD_PUBLIC` detected, prints remediation block pointing to `openclaw_fix_ssh_tailscale_only.sh`

**Remediation script**: `ops/openclaw_fix_ssh_tailscale_only.sh`
- Run as root on VPS: `sudo ./ops/openclaw_fix_ssh_tailscale_only.sh`
- Detects Tailscale IPv4, writes sshd drop-in config, validates, restarts, verifies
- Fail-closed: removes drop-in and restores original config on any failure

**Selftest**: `ops/tests/openclaw_doctor_selftest.sh` (20 tests):
- Feeds mock `ss` output to the Python analyzer
- Tests: sshd public/tailnet, tailscaled wildcard/tailnet, localhost, IPv6, boundary IPs, mixed scenarios

## Secure OpenAI Key Management

All Codex-powered workflows (`review_auto.sh`, `autoheal_codex.sh`, `ship_auto.sh`) source `ensure_openai_key.sh` which calls `openai_key.py --emit-env` to resolve the key. No manual key pasting required if Keychain is set; use `python3 ops/openai_key.py doctor` to validate.

**Canonical Keychain convention:**
- service: `ai-ops-runner`
- account: `OPENAI_API_KEY`
- Legacy entries (service `ai-ops-runner-openai`) are auto-migrated on first access.

**Public API (importable — `from ops.openai_key import load_openai_api_key`):**
- `load_openai_api_key()` → `str` — resolve key; raises `RuntimeError` if missing
- `load_openai_api_key_masked()` → `str` — shows `"sk-…abcd"` (prefix + last 4)
- `assert_openai_api_key_valid()` → `None` — minimal OpenAI API smoke call; raises on failure
- `get_openai_api_key()` → `str | None` — resolve from all sources (legacy compat)
- `set_openai_api_key(key)` → `bool` — store in keyring or Linux secrets file
- `delete_openai_api_key()` → `bool` — remove from all backends
- `openai_key_status(masked=True)` → `str` — return `"sk-…abcd"` or `"not configured"`
- `openai_key_source()` → `str` — return `"env"`, `"keychain"`, `"linux-file"`, or `"none"`

**CLI subcommands:**
- `python3 ops/openai_key.py status` — show source (env/keychain) + masked key
- `python3 ops/openai_key.py doctor` — run OpenAI API smoke test, exit nonzero on failure
- `python3 ops/openai_key.py set` — read key from stdin (no echo on TTY, pipe-safe) + store to Keychain
- `python3 ops/openai_key.py delete` — remove from all backends
- `python3 ops/openai_key.py --emit-env` — emit `export OPENAI_API_KEY=...` (pipe only, refused on TTY)

**Resolution order (deterministic, non-interactive):**
1. `OPENAI_API_KEY` env var — if already set, use immediately.
2. Python keyring — macOS Keychain / Linux SecretService (**no secret in argv**).
3. Linux file — `/etc/ai-ops-runner/secrets/openai_api_key` (chmod 600).
— Never prompts interactively. Use `set` subcommand to store a key. —

**Invariants:**
- Key **NEVER** printed to human-visible output. `status` shows masked only. `--emit-env` guarded by TTY check.
- Key never committed to git, never in stderr, **never in process argv**.
- `CODEX_SKIP=1` (simulated mode) bypasses key loading entirely.
- Fail-closed: if key unavailable, pipeline stops with human-readable instructions.
- Non-interactive: resolve chain never prompts; errors include HTTP status/body (masked).
- One-time bootstrap only: after initial setup, all reruns succeed without manual export.
- `ensure_openai_key.sh` uses `--emit-env` for safe shell capture (eval + scrub).
- Review gate prints masked key fingerprint for audit trail after loading.

## VPS Deployment Design

1. **Private-only**: No public ports. API on `127.0.0.1:8000` only, exposed to tailnet via `tailscale serve`.
2. **UFW**: Deny all incoming except Tailscale interface + WireGuard UDP.
3. **Review-gated updates**: `vps_self_update.sh` checks `LAST_REVIEWED_SHA == origin/main HEAD`; fail-closed.
4. **Systemd timers**: Update every 15 min, smoke test daily 06:00 UTC.
5. **Rollback**: If docker compose fails after update, automatic rollback to previous HEAD.
6. **Secrets**: Never in repo. Auth keys live on VPS in `/etc/ai-ops-runner/secrets/` (chmod 600).

## ORB Integration Design

1. **Repo allowlist** (`configs/repo_allowlist.yaml`): Only `algo-nt8-orb` is allowed
2. **Job allowlist** (`configs/job_allowlist.yaml`): ORB jobs have `requires_repo_allowlist: true`
3. **Wrapper scripts** (`orb_wrappers/`): Run inside read-only worktree, write outputs to `$ARTIFACT_DIR`
4. **Params**: Passed via `params.json` in artifact dir; executor injects as env vars (only `allowed_params` accepted)
5. **Invariants**: Every job records `read_only_ok` and `clean_tree_ok` in `artifact.json`
6. **Doctor 18/18**: Executor sets `core.hooksPath .githooks` in gitdir config at step 2a (before make_readonly); `orb_doctor.sh` also sets it as belt-and-suspenders
7. **SIZE_CAP → success-with-warning**: `orb_review_bundle.sh` auto-generates review packets on SIZE_CAP, exits 0 (success); executor merges `size_cap_meta.json` into `artifact.json` as `size_cap_fallback` and promotes `size_cap_exceeded`, `warnings`, `review_packets_archive`, `review_packets_dir` to top level; `FORCE_SIZE_CAP=1` available for deterministic testing

## Push Gate Design

The pre-push hook is the last line of defense. It has:
- **No bypass env vars** (all removed)
- **Simulated verdict rejection** (meta.simulated must be false)
- **Codex CLI provenance** (meta.codex_cli.version must be non-empty)
- **Exact range validation** (since_sha/to_sha match push range)
- **Baseline-advance allowance** (only docs/LAST_REVIEWED_SHA.txt diff tolerated)

## Security Model (NEVER change)

1. **No git push** — bare mirrors have push URL set to `DISABLED`
2. **Read-only worktrees** — ephemeral, pinned SHA, chmod -R a-w, clean-tree assertion
3. **Allowlisted commands only** — configs/job_allowlist.yaml
4. **Repo allowlist** — configs/repo_allowlist.yaml; ORB jobs reject non-listed repos
5. **Isolated outputs** — non-root, no docker.sock, read-only root filesystem
6. **MUTATION_DETECTED** — if worktree is dirty post-job, job fails with changed file list
7. **Private-only networking** — no public ports, Tailscale-only access, UFW deny incoming
8. **Review-gated VPS updates** — fail-closed if code hasn't been APPROVED by ship_auto
9. **Secure key management** — keys never in git, never in argv; auto-loaded from Keychain via stdin piping (macOS) or /etc secrets (Linux); fail-closed

## Canonical Command

```bash
./ops/ship_auto.sh
```

See `docs/CANONICAL_COMMANDS.md` for the full reference.

## Next Actions

1. Run `./ops/INSTALL_HOOKS.sh` to activate git hooks
2. Run `./ops/doctor_repo.sh` to verify repo health
3. Use `./ops/ship_auto.sh` for the standard ship workflow
4. **One-command OpenClaw apply**: `./ops/openclaw_apply_remote.sh` (syncs, builds, fixes SSH, verifies, Soma smoke)
5. **Install guard on VPS**: `sudo ./ops/openclaw_install_guard.sh` (enables 10-min timer)
6. **Check guard status**: `systemctl status openclaw-guard.timer` + `tail /var/log/openclaw_guard.log`
7. **Launch OpenClaw Console (production)**:
   ```bash
   python3 ops/openclaw_targets.py init         # one-time
   python3 ops/openclaw_console_token.py rotate  # one-time
   ./ops/openclaw_console_build.sh
   ./ops/openclaw_console_start.sh               # → http://127.0.0.1:8787
   ```
8. **Autostart at login**: `./ops/openclaw_console_install_macos_launchagent.sh`
9. **Console runbook**: `docs/OPENCLAW_CONSOLE_RUNBOOK.md`
10. Deploy to VPS: `VPS_SSH_TARGET=runner@<IP> TAILSCALE_AUTHKEY=tskey-... ./ops/vps_deploy.sh`
11. Check VPS health: `VPS_SSH_TARGET=runner@<IP> ./ops/vps_doctor.sh`
12. **Soma Kajabi snapshot**: `python3 -m services.soma_kajabi_sync.snapshot --product "Home User Library"`
13. **Soma smoke test**: `./ops/soma_smoke.sh`
14. **SMS status test**: `./ops/openclaw_sms.sh test`
15. **Soma runbook**: `docs/SOMA_KAJABI_RUNBOOK.md`
