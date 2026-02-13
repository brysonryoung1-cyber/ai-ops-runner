# OpenClaw Notifications

Outbound-only alerting for OpenClaw infrastructure events. **No inbound webhooks.**

## Platform: Pushover

Pushover was chosen for:
- Outbound HTTPS POST only (no inbound webhook surface)
- iOS/Android push notifications with priority levels
- Simple API (one curl call)
- No server-side infrastructure required

## Setup

### 1. Create Pushover Account

1. Sign up at https://pushover.net
2. Note your **User Key** (shown on dashboard)
3. Create an Application → note the **API Token**

### 2. Store Secrets

Secrets follow the key handling contract (env → keychain → file):

**macOS (Keychain)**:
```bash
# Store Pushover App Token
security add-generic-password -a PUSHOVER_APP_TOKEN -s ai-ops-runner -w "<YOUR_APP_TOKEN>" -U

# Store Pushover User Key
security add-generic-password -a PUSHOVER_USER_KEY -s ai-ops-runner -w "<YOUR_USER_KEY>" -U
```

**Linux (VPS)**:
```bash
sudo mkdir -p /etc/ai-ops-runner/secrets
echo -n "<YOUR_APP_TOKEN>" | sudo tee /etc/ai-ops-runner/secrets/pushover_app_token > /dev/null
echo -n "<YOUR_USER_KEY>" | sudo tee /etc/ai-ops-runner/secrets/pushover_user_key > /dev/null
sudo chmod 600 /etc/ai-ops-runner/secrets/pushover_*
```

**Environment (override)**:
```bash
export PUSHOVER_APP_TOKEN="..."
export PUSHOVER_USER_KEY="..."
```

### 3. Verify

```bash
./ops/openclaw_notify.sh --test
```

## Usage

### CLI

```bash
# Send a simple message
./ops/openclaw_notify.sh "Doctor FAIL on aiops-1: sshd public bind"

# Send with priority
./ops/openclaw_notify.sh --priority high "CRITICAL: Guard remediation failed"

# Send with title
./ops/openclaw_notify.sh --title "OpenClaw Doctor" "Check failed: tailscale_up"

# Test mode (dry-run)
./ops/openclaw_notify.sh --dry-run "Test message"
```

### Priority Levels

| Priority | Pushover Value | Use Case |
|----------|---------------|----------|
| `low` | -1 | SIZE_CAP warnings, informational |
| `normal` | 0 | Standard alerts (default) |
| `high` | 1 | Doctor failures, guard regressions |
| `emergency` | 2 | Remediation failures (requires acknowledgment) |

## Integration Points

### Doctor (`openclaw_doctor.sh`)

On any FAIL check:
- Sends alert with: hostname, check_id, short reason, remediation command
- Example: `"[aiops-1] FAIL: public_port_audit — sshd on 0.0.0.0:22. Run: sudo ./ops/openclaw_fix_ssh_tailscale_only.sh"`

### Guard (`openclaw_guard.sh`)

On first regression detection:
- Sends alert with: hostname, failing check, remediation status
- Rate-limited: max once per 30 minutes per check_id
- Rate-limit state stored in `/tmp/openclaw_notify_ratelimit/`

### Job Failures

On job failure:
- Alert with job_id + artifact path
- `"[aiops-1] JOB FAIL: job_abc123 — artifacts/abc123/"`

### SIZE_CAP Warnings

On SIZE_CAP exceeded:
- WARN-level alert (not FAIL)
- `"[aiops-1] WARN: SIZE_CAP exceeded for job_abc123 — review packets generated"`

## Rate Limiting

- State directory: `/tmp/openclaw_notify_ratelimit/`
- Key: SHA256 of check_id
- Window: 30 minutes (configurable via `OPENCLAW_NOTIFY_RATE_LIMIT_SEC`)
- First occurrence always sends; repeats suppressed within window
- Rate-limit state is ephemeral (cleared on reboot)

## Security

- Outbound ONLY: HTTPS POST to `api.pushover.net/1/messages.json`
- No inbound webhooks, no callback URLs
- Secrets loaded via key handling contract (never printed)
- Dry-run mode available for testing without sending
- No PII in alert messages (hostnames and check IDs only)
