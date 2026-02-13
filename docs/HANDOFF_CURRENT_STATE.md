# Handoff — Current State

## Last Updated

2026-02-13

## Status

All systems operational. Docker smoke test passing. Full ops/review/ship framework active. ORB integration jobs implemented and tested. VPS deployment automation added (private-only, Tailscale-only). ORB doctor passes 18/18 in runner context (hooksPath hardening). SIZE_CAP fallback auto-generates review-packet artifacts (now exits 0 = success-with-warning). openclaw_doctor uses tailnet-aware port audit. Automated sshd remediation available. macOS Keychain storage uses stdin piping (no argv leak). Executor hooksPath logging hardened (check=True, explicit failure logging).

## Recent Changes

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
├── openai_key.py             # Secure cross-platform OpenAI key loader (env/Keychain/file)
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
    ├── openai_key_selftest.sh
    └── test_openai_key.py

configs/
├── job_allowlist.yaml        # Allowlisted job types (incl. ORB jobs)
└── repo_allowlist.yaml       # Allowlisted target repos (algo-nt8-orb)

docs/
├── DEPLOY_VPS.md             # VPS deployment guide
├── LAST_REVIEWED_SHA.txt
├── REVIEW_WORKFLOW.md
├── REVIEW_PACKET.md
├── HANDOFF_CURRENT_STATE.md
└── CANONICAL_COMMANDS.md

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

All Codex-powered workflows (`review_auto.sh`, `autoheal_codex.sh`, `ship_auto.sh`) source `ensure_openai_key.sh` which calls `openai_key.py` to resolve the key.

**Resolution order (fail-fast):**
1. `OPENAI_API_KEY` env var — if already set, use immediately.
2. macOS Keychain — service `ai-ops-runner-openai`, account `openai_api_key` (read via `security find-generic-password`, **no secret in argv**).
3. Linux file — `/etc/ai-ops-runner/secrets/openai_api_key` (chmod 600).
4. macOS interactive prompt — uses `getpass` (no echo), stores in Keychain via `security add-generic-password` with stdin piping (**no secret in argv**).

**Invariants:**
- Key never committed to git, never echoed to terminal, never in stderr, **never in process argv**.
- `CODEX_SKIP=1` (simulated mode) bypasses key loading entirely.
- Fail-closed: if key unavailable, pipeline stops with human-readable instructions.
- One-time bootstrap only: after initial setup, all reruns succeed without manual export.
- No external Python dependencies required — uses macOS `security` CLI with stdin piping.

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
4. Deploy to VPS: `VPS_SSH_TARGET=runner@<IP> TAILSCALE_AUTHKEY=tskey-... ./ops/vps_deploy.sh`
5. Check VPS health: `VPS_SSH_TARGET=runner@<IP> ./ops/vps_doctor.sh`
