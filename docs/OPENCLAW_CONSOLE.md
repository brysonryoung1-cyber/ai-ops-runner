# OpenClaw Console

A private, macOS-style web UI for managing the OpenClaw stack on aiops-1 via Tailscale SSH.

## Quick Start (Local Mac)

```bash
./ops/openclaw_console_up.sh
```

Then open: **http://127.0.0.1:8787**

## Quick Start (VPS + Phone via Tailscale)

```bash
# On aiops-1 VPS:
cd /opt/ai-ops-runner

# Build and start console (127.0.0.1 only)
./ops/openclaw_console_build.sh
./ops/openclaw_console_start.sh

# Expose to tailnet via tailscale serve
sudo tailscale serve --bg --https=443 http://127.0.0.1:8787

# Get your tailnet URL
echo "Console URL: https://$(tailscale status --self --json | python3 -c 'import sys,json; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')"
```

Then open the tailnet URL on your phone (Tailscale app must be connected).

## What It Does

The console provides a clean dashboard for monitoring and managing the OpenClaw security stack on the aiops-1 VPS, without ever leaving your Mac or phone.

### Pages

| Page | Description |
|------|-------------|
| **Overview** | Doctor status (PASS/FAIL), sshd bind summary, guard timer status, Docker stack health |
| **Logs** | Tail the last 200 lines of openclaw-guard service journal |
| **Artifacts** | List the latest artifact job directories with sizes |
| **Actions** | Buttons for Doctor, Apply, Guard Install, and Port Audit |

### Allowlisted Operations

Only these exact commands can be executed (no arbitrary command injection):

| Action | Remote Command |
|--------|---------------|
| `doctor` | `cd /opt/ai-ops-runner && ./ops/openclaw_doctor.sh` |
| `apply` | `cd /opt/ai-ops-runner && ./ops/openclaw_apply_remote.sh` |
| `guard` | `cd /opt/ai-ops-runner && sudo ./ops/openclaw_install_guard.sh` |
| `ports` | `ss -lntp` |
| `timer` | `systemctl status openclaw-guard.timer --no-pager` |
| `journal` | `journalctl -u openclaw-guard.service -n 200 --no-pager` |
| `artifacts` | `ls -1dt artifacts/* ... && du -sh artifacts/*` |

## Security Model

- **Localhost only**: The server binds to `127.0.0.1:8787`. It is never exposed on `0.0.0.0`.
- **Token auth**: All API requests require `X-OpenClaw-Token` header (Keychain-backed).
- **CSRF protection**: Origin validation on all POST/mutating requests.
- **Action lock**: Prevents overlapping execution of the same action.
- **Audit log**: Every action execution is logged (timestamp, actor fingerprint, action, exit code, duration).
- **Tailscale CGNAT validation**: `AIOPS_HOST` must be in `100.64.0.0/10`. Anything outside is rejected.
- **No arbitrary commands**: Only allowlisted operations. Backend rejects anything not in the list.
- **SSH BatchMode**: All connections use `BatchMode=yes` — no interactive prompts.
- **No secrets in code**: `.env.local` is gitignored. Tokens loaded from Keychain.
- **Payload limits**: API requests capped at 1MB.

## VPS Deployment (aiops-1 behind Tailscale)

### Option A: Direct Process (Recommended)

```bash
# On aiops-1:
cd /opt/ai-ops-runner

# One-time setup
python3 ops/openclaw_console_token.py rotate
python3 ops/openclaw_targets.py init

# Build and run
./ops/openclaw_console_build.sh
./ops/openclaw_console_start.sh
# → http://127.0.0.1:8787 (local only)

# Expose to tailnet
sudo tailscale serve --bg --https=443 http://127.0.0.1:8787
```

### Option B: Docker Compose

Add to your deployment:

```bash
# On aiops-1:
cd /opt/ai-ops-runner
docker compose -f docker-compose.yml -f docker-compose.console.yml up -d --build

# Expose to tailnet
sudo tailscale serve --bg --https=443 http://127.0.0.1:8787
```

### Phone Access

1. Install **Tailscale** on your phone (iOS/Android)
2. Sign in with the same tailnet
3. Open the console URL: `https://aiops-1.<tailnet-name>.ts.net`
4. Enter your console token when prompted

### Tailscale Serve Commands

```bash
# Enable HTTPS proxy (443 → 8787)
sudo tailscale serve --bg --https=443 http://127.0.0.1:8787

# Check status
sudo tailscale serve status

# Disable
sudo tailscale serve --https=443 off
```

The resulting URL will be: `https://aiops-1.<your-tailnet>.ts.net`

### Doctor Verification

After deployment, the doctor checks that the console is bound correctly:

```bash
./ops/openclaw_doctor.sh
# Look for: "PASS: Console bound to 127.0.0.1:8787 (private-only)"
```

## Configuration

Copy the example env file and adjust if needed:

```bash
cp apps/openclaw-console/.env.example apps/openclaw-console/.env.local
```

Variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `AIOPS_HOST` | `100.123.61.57` | Tailscale IP of aiops-1 (must be in 100.64.0.0/10) |
| `AIOPS_USER` | `root` | SSH user for the remote host |
| `OPENCLAW_CONSOLE_PORT` | `8787` | Console port |
| `OPENCLAW_CONSOLE_TOKEN` | *(Keychain)* | Auth token (loaded by start.sh) |
| `OPENCLAW_TAILSCALE_HOSTNAME` | *(optional)* | Tailnet FQDN for CORS (e.g., `aiops-1.tail1234.ts.net`) |

## Prerequisites

- **Node.js** ≥ 18
- **Tailscale** running and connected (so SSH can reach aiops-1)
- **SSH key** configured for `root@<AIOPS_HOST>` (Tailscale SSH or standard key)

## Audit Log

Every action execution produces an audit entry in `data/audit.jsonl`:

```json
{
  "timestamp": "2026-02-13T12:00:00.000Z",
  "actor": "tok_a1b2c3d4",
  "action_name": "doctor",
  "params_hash": "abc123def456",
  "exit_code": 0,
  "duration_ms": 2345
}
```

The actor field is a SHA256 fingerprint of the token (never the token itself).

## Install as App Window

For a native app-like experience on macOS, open Safari, navigate to `http://127.0.0.1:8787`, then:

1. **Safari**: File → Add to Dock (macOS Sonoma+)
2. **Chrome**: ⋮ → More Tools → Create Shortcut → "Open as window"

## Troubleshooting

**"SSH Connection Failed"**:
- Ensure Tailscale is running: `tailscale status`
- Test manually: `ssh root@100.123.61.57 'echo ok'`
- Check that your SSH key is configured for the target host

**"AIOPS_HOST is not in Tailscale range"**:
- Verify your `.env.local` contains a valid Tailscale IP (100.64.x.x – 100.127.x.x)

**"Action is already running" (409)**:
- A mutating action (doctor, apply, guard) is still executing
- Wait for it to complete or check the audit log for stuck actions

**Port 8787 in use**:
- Kill the existing process: `lsof -ti:8787 | xargs kill -9`
- Or change the port via `OPENCLAW_CONSOLE_PORT` env var
