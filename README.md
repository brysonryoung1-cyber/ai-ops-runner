# ai-ops-runner

A 24/7 VPS assistant job-runner that automates testing and analysis against target repositories **without being able to modify or push code**.

## Security Guarantees

1. **No git push** — bare mirrors have push URL set to `DISABLED`; no write tokens are ever stored.
2. **Read-only worktrees** — every job runs in an ephemeral `git worktree` at a pinned SHA; the worktree is made read-only (`chmod -R a-w`, preserving execute bits). After the job completes, a clean-tree assertion (`git status --porcelain` + `git diff --exit-code`) must pass or the job is marked as error.
3. **Allowlisted commands only** — only commands defined in `configs/job_allowlist.yaml` can execute; callers cannot override argv.
4. **Isolated outputs** — artifacts are written only to `/artifacts/<job_id>/`; the repo is mounted read-only; containers run as non-root (uid 1000) with no Docker socket access and a read-only root filesystem.

## Architecture

```
┌──────────┐   POST /jobs    ┌─────────────────┐
│  Client   │───────────────►│  test_runner_api │
└──────────┘                 └────────┬────────┘
                                      │ Redis queue
                              ┌───────▼────────┐
                              │test_runner_worker│
                              └───────┬────────┘
                                      │
                    ┌─────────────────┼─────────────────┐
                    │                 │                  │
              ┌─────▼─────┐  ┌───────▼───────┐  ┌──────▼──────┐
              │ Bare Mirror│  │Ephemeral      │  │  Artifacts  │
              │ /repos/    │  │Worktree /work/│  │ /artifacts/ │
              └───────────┘  └───────────────┘  └─────────────┘
```

## Quick Start

```bash
# Start all services
docker compose up -d --build

# Run smoke test
./ops/runner_smoke.sh

# Submit a specific job
./ops/runner_submit_job.sh local_echo my-repo https://github.com/user/repo.git abc123
```

## API

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/healthz` | Health check → `{"ok": true}` |
| `POST` | `/jobs` | Submit a job → `{job_id, artifact_dir, status}` |
| `GET` | `/jobs/{id}` | Get job status and details |
| `GET` | `/jobs/{id}/logs?stream=stdout\|stderr&tail=200` | Tail job logs |

### Submit Job Request

```json
{
  "job_type": "local_echo",
  "repo_name": "my-repo",
  "remote_url": "https://github.com/user/repo.git",
  "sha": "abc123def456",
  "idempotency_key": "optional-unique-key"
}
```

## Allowlisted Jobs

Defined in `configs/job_allowlist.yaml`:

- **local_echo** — simple echo + uname (60s timeout)
- **orb_ops_selftests** — run ORB self-tests (1800s timeout)
- **orb_review_auto_nopush** — run review_auto with --no-push (1800s timeout)

## Ops Automation (Ship Autopilot)

This repo includes a full review + ship automation framework. No manual paste of review packets — everything is self-running.

### First-Time Setup

```bash
# Install git hooks (pre-push gate + post-commit trigger)
./ops/INSTALL_HOOKS.sh

# Verify repo health
./ops/doctor_repo.sh
```

### Standard Workflow

```bash
# Implement → commit → ship
./ops/ship_auto.sh
```

This single command:
1. Runs unit tests (pytest + docker compose validation)
2. Runs Codex review (`review_auto.sh --no-push`)
3. If BLOCKED → auto-heals (applies fixes) → re-tests → re-reviews (bounded attempts)
4. If APPROVED → advances baseline → pushes

### Individual Commands

```bash
# Review only (no push)
./ops/review_auto.sh --no-push

# Generate review bundle for inspection
./ops/review_bundle.sh --since "$(cat docs/LAST_REVIEWED_SHA.txt)"

# Advance baseline + push (after external approval)
./ops/review_finish.sh

# Check repo health
./ops/doctor_repo.sh
```

### Push Gate

The pre-push hook enforces that pushes are impossible without an APPROVED verdict for the exact commit range being pushed. If blocked:

```bash
./ops/review_auto.sh
```

### Review Artifacts

Review artifacts are stored locally in `review_packets/` (gitignored, never committed):

```
review_packets/<timestamp>/
├── REVIEW_BUNDLE.txt      # Diff sent for review
├── CODEX_VERDICT.json     # Strict JSON verdict
└── META.json              # Metadata (range, timestamp, mode)
```

## Development

```bash
# Install deps for local testing
cd services/test_runner
pip install -r requirements.txt

# Run unit tests
pytest -q

# Validate docker compose
docker compose config

# Run ops selftests
bash ops/tests/review_bundle_selftest.sh
bash ops/tests/review_auto_selftest.sh
bash ops/tests/review_finish_selftest.sh
bash ops/tests/ship_auto_selftest.sh
```

## Repo Structure

```
├── docker-compose.yml
├── configs/
│   └── job_allowlist.yaml
├── docs/
│   ├── LAST_REVIEWED_SHA.txt
│   ├── REVIEW_WORKFLOW.md
│   ├── REVIEW_PACKET.md
│   └── HANDOFF_CURRENT_STATE.md
├── ops/
│   ├── review_bundle.sh
│   ├── review_auto.sh
│   ├── review_finish.sh
│   ├── ship_auto.sh
│   ├── autoheal_codex.sh
│   ├── doctor_repo.sh
│   ├── INSTALL_HOOKS.sh
│   ├── runner_smoke.sh
│   ├── runner_submit_job.sh
│   ├── schemas/
│   │   └── codex_review_verdict.schema.json
│   └── tests/
│       ├── review_bundle_selftest.sh
│       ├── review_auto_selftest.sh
│       ├── review_finish_selftest.sh
│       └── ship_auto_selftest.sh
├── .githooks/
│   ├── pre-push
│   └── post-commit
├── review_packets/          (gitignored)
├── services/test_runner/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── test_runner/
│   │   ├── api.py
│   │   ├── worker.py
│   │   ├── db.py
│   │   ├── models.py
│   │   ├── allowlist.py
│   │   ├── git_mirror.py
│   │   ├── executor.py
│   │   ├── security.py
│   │   ├── artifacts.py
│   │   └── util.py
│   ├── migrations/
│   │   └── 001_init.sql
│   └── tests/
│       ├── test_allowlist.py
│       ├── test_artifacts.py
│       ├── test_git_push_disabled.py
│       └── test_readonly_worktree_asserts.py
└── README.md
```
