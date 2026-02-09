-- 001_init.sql: Create the jobs table for the test runner.

CREATE TABLE IF NOT EXISTS jobs (
    job_id          TEXT PRIMARY KEY,
    repo_name       TEXT NOT NULL,
    remote_url      TEXT NOT NULL,
    sha             TEXT NOT NULL,
    job_type        TEXT NOT NULL,
    argv            JSONB NOT NULL DEFAULT '[]',
    timeout_sec     INTEGER NOT NULL DEFAULT 600,
    status          TEXT NOT NULL DEFAULT 'queued',
    idempotency_key TEXT UNIQUE,
    started_at      TEXT,
    finished_at     TEXT,
    duration_ms     INTEGER,
    exit_code       INTEGER,
    hostname        TEXT,
    trace_id        TEXT,
    input_hash      TEXT,
    allowlist_hash  TEXT,
    artifact_dir    TEXT,
    created_at      TEXT NOT NULL DEFAULT (now()::text)
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs (status);
CREATE INDEX IF NOT EXISTS idx_jobs_idempotency ON jobs (idempotency_key)
    WHERE idempotency_key IS NOT NULL;
