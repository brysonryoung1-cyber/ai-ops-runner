# Handoff — Current State

## Last Updated

2026-02-12

## Status

All systems operational. Docker smoke test passing. Full ops/review/ship framework active. ORB integration jobs implemented and tested. VPS deployment automation added (private-only, Tailscale-only). ORB doctor passes 18/18 in runner context (hooksPath hardening). SIZE_CAP fallback auto-generates review-packet artifacts.

## Recent Changes

- **ORB doctor hooksPath hardening**: `orb_doctor.sh` now sets `core.hooksPath .githooks` in the gitdir config before running the ORB doctor, eliminating the false finding in runner context. Writes to gitdir (outside worktree), so mutation detection is not tripped.
- **SIZE_CAP → review packets**: When `orb_review_bundle` hits exit code 6 (SIZE_CAP), the wrapper now auto-generates:
  - Per-file packet diffs in `review_packets/<stamp>/packet_NNN.txt`
  - `HOW_TO_PASTE.txt` with review instructions
  - `ORB_REVIEW_PACKETS.tar.gz` archive
  - `ORB_REVIEW_PACKETS_README.txt` guide
  - `size_cap_meta.json` (merged into `artifact.json` as `size_cap_fallback`)
- **Executor**: Reads `size_cap_meta.json` from artifact dir and includes `size_cap_fallback` field in `artifact.json`
- **Tests**: Added pytest tests for hooksPath config (clean-tree safe) and SIZE_CAP packet generation (5 new tests)
- **Selftest**: Extended `orb_integration_selftest.sh` with checks for hooksPath hardening, SIZE_CAP packet generation, and executor integration
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
    └── orb_integration_selftest.sh

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

## VPS Deployment Design

1. **Private-only**: No public ports. API on `127.0.0.1:8000` only, exposed to tailnet via `tailscale serve`.
2. **UFW**: Deny all incoming except Tailscale interface + WireGuard UDP.
3. **Review-gated updates**: `vps_self_update.sh` checks `LAST_REVIEWED_SHA == origin/main HEAD`; fail-closed.
4. **Systemd timers**: Update every 15 min, smoke test daily 06:00 UTC.
5. **Rollback**: If docker compose fails after update, automatic rollback to previous HEAD.
6. **Secrets**: Never in repo. Auth keys live on VPS in `/etc/ai-ops-runner/env` (chmod 600).

## ORB Integration Design

1. **Repo allowlist** (`configs/repo_allowlist.yaml`): Only `algo-nt8-orb` is allowed
2. **Job allowlist** (`configs/job_allowlist.yaml`): ORB jobs have `requires_repo_allowlist: true`
3. **Wrapper scripts** (`orb_wrappers/`): Run inside read-only worktree, write outputs to `$ARTIFACT_DIR`
4. **Params**: Passed via `params.json` in artifact dir; executor injects as env vars (only `allowed_params` accepted)
5. **Invariants**: Every job records `read_only_ok` and `clean_tree_ok` in `artifact.json`
6. **Doctor 18/18**: `orb_doctor.sh` pre-sets `core.hooksPath .githooks` in gitdir config (outside worktree, clean-tree safe)
7. **SIZE_CAP → packets**: `orb_review_bundle.sh` auto-generates review packets on exit 6; executor merges `size_cap_meta.json` into `artifact.json` as `size_cap_fallback`

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
