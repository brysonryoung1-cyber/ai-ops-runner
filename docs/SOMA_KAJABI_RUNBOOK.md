# Soma Kajabi Library Ownership — Runbook

## Overview

The Soma Kajabi Sync workflow provides first-class management of Kajabi course libraries (Home User Library + Practitioner Library). It captures snapshots, harvests video metadata from Gmail, and produces diff-based mirror reports.

**Components:**
- `services/soma_kajabi_sync/` — Python service with CLI entrypoints
- `ops/soma_smoke.sh` — Smoke test (no credentials required)
- `ops/openclaw_sms.sh` — SMS CLI driver
- `ops/openclaw_notify_sms.sh` — SMS alert integration
- Console "Soma" page — Web UI for all operations

## Quick Start

### 1. Smoke Test (no credentials required)

```bash
./ops/soma_smoke.sh
```

This verifies all modules import, artifact writing works, and integrity checks pass. Uses synthetic data.

### 2. One-Command VPS Apply (includes Soma smoke)

```bash
./ops/openclaw_apply_remote.sh
```

Pulls latest code, rebuilds, runs doctor, runs Soma smoke.

### 3. Kajabi Snapshot

```bash
# From VPS or local (with secrets configured):
python3 -m services.soma_kajabi_sync.snapshot --product "Home User Library"
python3 -m services.soma_kajabi_sync.snapshot --product "Practitioner Library"
```

Output: `artifacts/soma/<run_id>/snapshot.json` (+ sha256 sidecar)

### 4. Gmail Video Harvest

```bash
python3 -m services.soma_kajabi_sync.harvest
```

Output:
- `artifacts/soma/<run_id>/gmail_video_index.json`
- `artifacts/soma/<run_id>/video_manifest.csv`

### 5. Mirror Report

```bash
python3 -m services.soma_kajabi_sync.mirror --dry-run
```

Output:
- `artifacts/soma/<run_id>/mirror_report.json`
- `artifacts/soma/<run_id>/changelog.md`

### 6. SMS Status

```bash
./ops/openclaw_sms.sh test
```

## Secret Setup

All secrets follow the standard resolution order: **env → macOS Keychain → Linux file**.

### Required Secrets

| Secret | Purpose | File Path |
|--------|---------|-----------|
| `KAJABI_SESSION_TOKEN` | Kajabi API/session auth | `/etc/ai-ops-runner/secrets/kajabi_session_token` |
| `GMAIL_USER` | Gmail IMAP login | `/etc/ai-ops-runner/secrets/gmail_user` |
| `GMAIL_APP_PASSWORD` | Gmail app password | `/etc/ai-ops-runner/secrets/gmail_app_password` |
| `TWILIO_ACCOUNT_SID` | Twilio SMS account | `/etc/ai-ops-runner/secrets/twilio_account_sid` |
| `TWILIO_AUTH_TOKEN` | Twilio SMS auth | `/etc/ai-ops-runner/secrets/twilio_auth_token` |
| `TWILIO_FROM_NUMBER` | Twilio sender number | `/etc/ai-ops-runner/secrets/twilio_from_number` |
| `SMS_ALLOWLIST` | Comma-separated phone numbers | `/etc/ai-ops-runner/secrets/sms_allowlist` |

### Setting Up Secrets (Linux VPS)

```bash
# Create secrets directory (root-only, mode 700)
sudo mkdir -p /etc/ai-ops-runner/secrets
sudo chmod 700 /etc/ai-ops-runner/secrets

# Write each secret (mode 600)
echo "your-kajabi-session-token" | sudo tee /etc/ai-ops-runner/secrets/kajabi_session_token > /dev/null
sudo chmod 600 /etc/ai-ops-runner/secrets/kajabi_session_token

# Repeat for each secret...
echo "+15551234567,+15559876543" | sudo tee /etc/ai-ops-runner/secrets/sms_allowlist > /dev/null
sudo chmod 600 /etc/ai-ops-runner/secrets/sms_allowlist
```

### Setting Up Secrets (macOS Keychain)

```bash
# Store in Keychain (no secret in argv — piped via stdin)
echo -n "your-token" | security add-generic-password -a KAJABI_SESSION_TOKEN -s ai-ops-runner -T "" -w
```

### Kajabi Session Token Capture (legacy)

The Kajabi session token must be captured from a browser session:

1. Log in to `https://app.kajabi.com` in Chrome
2. Open DevTools → Application → Cookies
3. Copy the `_kjb_session` cookie value
4. Store it as `KAJABI_SESSION_TOKEN` using the methods above

**Important:** Session tokens expire. When operations fail with "session expired", re-capture the token.

### Kajabi storage_state (Playwright, preferred for Phase 0)

- **Path**: `/etc/ai-ops-runner/secrets/soma_kajabi/kajabi_storage_state.json` (mode 600, root:root). No contents in logs.
- **Playwright**: Installed for hostd via `ops/scripts/ensure_hostd_venv_playwright.sh` (called by `install_openclaw_hostd.sh`). Required for Kajabi capture fallback. No manual pip installs on aiops-1 needed.
- **Capture**: On aiops-1 run `python3 ops/scripts/kajabi_capture_storage_state.py` (headed). Sign in at https://app.kajabi.com and land on the dashboard; script saves to `/tmp/kajabi_storage_state.json`. Then install: `sudo cp /tmp/kajabi_storage_state.json /etc/ai-ops-runner/secrets/soma_kajabi/kajabi_storage_state.json && sudo chmod 600 ...`
- **Full unblock**: `./ops/csr_soma_unblock.sh` (run on aiops-1) runs Phase 0 prep, Kajabi capture + Gmail device flow, connectors check, Phase 0, Zane punch list, re-enables autopilot. Pauses only for human: Kajabi login, Gmail client upload, device approval.

### Phase 0: Kajabi-only mode (Gmail optional)

Phase 0 succeeds with **Kajabi-only** when Gmail OAuth is not configured. Missing `/etc/ai-ops-runner/secrets/soma_kajabi/gmail_oauth.json` no longer causes `CONNECTOR_NOT_CONFIGURED` or non-zero exit. When Gmail is skipped:
- `result.json` includes `gmail_status: "skipped"` and `gmail_reason: "oauth token not found at ..."`
- `gmail_harvest.jsonl` is written as a single metadata line so downstream consumers can rely on file existence
- `video_manifest.csv` is empty; Zane finish plan marks Gmail-dependent items as BLOCKED

### Gmail OAuth (Phase 0 / Soma)

- **Client JSON**: Upload `gmail_client.json` (Google OAuth Desktop or Limited Input Device app) via **HQ → Settings → Connectors → Gmail OAuth**. Stored on-box at `/etc/ai-ops-runner/secrets/soma_kajabi/gmail_client.json` (path only; no contents in logs).
- **Token**: After device flow (Start → user approves at verification URL → Finalize), refresh token is written to `/etc/ai-ops-runner/secrets/soma_kajabi/gmail_oauth.json`. Do not print or log file contents.

### Gmail App Password (IMAP fallback)

1. Go to Google Account → Security → 2-Step Verification → App passwords
2. Generate a new app password for "Mail"
3. Store as `GMAIL_APP_PASSWORD`

## Artifact Structure

All artifacts are written to `artifacts/soma/<run_id>/`:

```
artifacts/soma/
├── snapshot_20260215T120000Z_a1b2c3d4/
│   ├── _manifest.json          # Run metadata
│   ├── snapshot.json           # Kajabi product structure
│   └── snapshot.json.sha256    # Integrity sidecar
├── harvest_20260215T130000Z_e5f6g7h8/
│   ├── _manifest.json
│   ├── gmail_video_index.json
│   ├── gmail_video_index.json.sha256
│   ├── video_manifest.csv
│   └── video_manifest.csv.sha256
└── mirror_20260215T140000Z_i9j0k1l2/
    ├── _manifest.json
    ├── mirror_report.json
    ├── mirror_report.json.sha256
    └── changelog.md
```

### Video Manifest CSV Columns

| Column | Description |
|--------|-------------|
| `video_id` | Unique video identifier |
| `title` | Video title |
| `source_email_id` | Gmail message ID |
| `date_received` | Date the email was received |
| `status` | `mapped`, `unmapped`, or `raw_needs_review` |
| `kajabi_product` | Target Kajabi product (if mapped) |
| `kajabi_category` | Target category (if mapped) |
| `file_url` | Direct URL to video file |
| `notes` | Additional notes |

## SMS Commands

### Outbound Alerts

Sent automatically on events:

| Event | Trigger |
|-------|---------|
| `WORKFLOW_SUCCESS` | Soma workflow completed successfully |
| `WORKFLOW_FAIL` | Soma workflow failed |
| `DOCTOR_FAIL` | Doctor check failed |
| `GUARD_FAIL` | Guard remediation needed |
| `NIGHTLY_FAIL` | Nightly jobs failed |
| `SIZE_CAP_WARN` | Review bundle exceeded size cap |

### Inbound Commands

Text these commands to the Twilio number:

| Command | Action |
|---------|--------|
| `STATUS` | Show system status summary |
| `RUN_SNAPSHOT` | Trigger Home Library snapshot |
| `RUN_HARVEST` | Trigger Gmail video harvest |
| `RUN_MIRROR` | Trigger mirror operation |
| `LAST_ERRORS` | Show last 5 error messages |

**Rate limits:** 1 command per minute per sender. Outbound alerts rate-limited to 1 per 30 minutes per event type.

## Console UX

The Soma section in the OpenClaw Console provides:

- **Status cards**: Last run, total runs, needs review count
- **Action buttons**: Snapshot (Home/Practitioner), Harvest, Mirror, Status
- **Result viewer**: Full output with PASS/FAIL indicators
- **Refresh**: Manual status refresh

Access via sidebar → "Soma" or navigate to `/soma`.

## Docker

For containerized runs:

```bash
docker compose -f docker-compose.yml -f docker-compose.soma.yml run --rm soma_sync \
  python -m soma_kajabi_sync.snapshot --smoke
```

## Testing

### Unit Tests

```bash
cd services/soma_kajabi_sync
python3 -m pytest tests/ -v
```

### Smoke Test

```bash
./ops/soma_smoke.sh
```

### Stability verification (aiops-1)

```bash
./ops/stability_verify_aiops1.sh
```

Runs on aiops-1 to confirm guard timer produces PASS and litellm-proxy stays healthy for ~3 minutes. Exit 0 = STABLE, 1 = UNSTABLE.

### Selftests

```bash
./ops/tests/soma_smoke_selftest.sh
./ops/tests/openclaw_sms_selftest.sh
```

## Kajabi via Exit Node (Laptop Mode)

To reduce Cloudflare challenges, aiops-1 can route Kajabi traffic through a Mac laptop as a Tailscale exit node. No Kajabi API purchase required.

### Requirements

- **Mac laptop**: Tailscale installed, exit node advertised (`tailscale up --advertise-exit-node --accept-routes`)
- **Tailscale admin**: Approve exit node for the Mac in Tailscale Admin → Machines → Edit route settings → Use as exit node
- **Laptop on/awake** during runs

### Config

Create on aiops-1:

```bash
# Use short hostname (tailscale up --exit-node requires IP or unique node name, not full MagicDNS)
echo "brysons-macbook-pro" | sudo tee /etc/ai-ops-runner/config/soma_kajabi_exit_node.txt
```

Or set `KAJABI_EXIT_NODE` env (MagicDNS hostname or Tailscale IP).

### Behavior

- **soma_kajabi_unblock_and_run**, **soma_kajabi_discover**, **soma_kajabi_snapshot_debug** run via `ops/with_exit_node.sh`
- If config missing: runs without exit node (fallback to interactive noVNC if Cloudflare blocks)
- If exit node unreachable (laptop off): fail-closed with `EXIT_NODE_OFFLINE` — "Turn on laptop (keep awake) and rerun"
- Wrapper ALWAYS restores previous exit-node state (trap) even on failure

### Verify

```bash
# On aiops-1: public IP before/after (optional)
./ops/what_is_my_ip.sh
```

### When Offline

If `EXIT_NODE_OFFLINE` triggers: turn on/keep awake the Mac exit node (Tailscale running), then rerun `soma_kajabi_unblock_and_run`.

## Troubleshooting

### "Kajabi session expired"
Re-capture the session token from browser DevTools.

### "Gmail IMAP login failed"
1. Verify `GMAIL_USER` and `GMAIL_APP_PASSWORD` are correct
2. Ensure "Less secure app access" or app password is configured
3. Check for Google security alerts

### "SMS send failed"
1. Run `./ops/openclaw_sms.sh test` to verify Twilio config
2. Check Twilio dashboard for account status
3. Verify `SMS_ALLOWLIST` contains valid phone numbers

### "Module import failed"
Ensure you're running from the repo root: `cd /opt/ai-ops-runner`. If hostd lacks Playwright, run `sudo ./ops/install_openclaw_hostd.sh` to install the managed venv with Playwright + Chromium.

### "No snapshot found" (mirror)
Run snapshot for both products before running mirror:
```bash
python3 -m services.soma_kajabi_sync.snapshot --product "Home User Library"
python3 -m services.soma_kajabi_sync.snapshot --product "Practitioner Library"
python3 -m services.soma_kajabi_sync.mirror --dry-run
```

## Security

- **Secrets**: Never printed raw. Resolved via env → Keychain → file. Stored in `/etc/ai-ops-runner/secrets/` (mode 700 dir, 600 files).
- **SMS allowlist**: Fail-closed — empty allowlist denies all inbound commands.
- **Rate limiting**: Inbound 1/min per sender, outbound 30min per event type.
- **Tailscale-only**: Console and SMS webhook only accessible via Tailscale.
- **No plaintext passwords in repo**: All secrets external.
- **Idempotent**: All operations can be safely re-run.
- **Fail-closed**: Missing credentials → operation fails with clear error.
