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

## Development

```bash
# Install deps for local testing
cd services/test_runner
pip install -r requirements.txt

# Run unit tests
pytest -q

# Validate docker compose
docker compose config
```

## Repo Structure

```
├── docker-compose.yml
├── configs/
│   └── job_allowlist.yaml
├── ops/
│   ├── runner_smoke.sh
│   └── runner_submit_job.sh
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
