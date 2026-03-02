# Next Up — Research Lane (Proposed)

**Created:** 2026-03-02 (CSR Phase 4)

## Proposed Action(s)

- **daily_research_scout** — Periodic research discovery (e.g., Zane email, Kajabi updates)
- **discord_recommendations** — Push curated recommendations to Discord

## Safety Gates (Fail-Closed)

- Rate limits: max N runs/day, max M Discord messages/day
- Allowlisted domains only (e.g., mykajabi.com, app.kajabi.com)
- Approval requirement: human confirm before first run
- No secrets in output; no PII in Discord

## Discord Message Format

- Subject line: `[OpenClaw] <action> — <summary>`
- Body: link to artifact, run_id, timestamp
- Max length: 512 chars

## Minimal v0 Milestone

1. Define action_registry entry (dry_run only)
2. Add rate-limit config
3. Add allowlist config
4. Single smoke test (mock Discord)
