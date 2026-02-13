# OpenClaw Console

A private, macOS-style web UI for managing the OpenClaw stack on aiops-1 via Tailscale SSH.

## Quick Start

```bash
./ops/openclaw_console_up.sh
```

Then open: **http://127.0.0.1:8787**

## What It Does

The console provides a clean dashboard for monitoring and managing the OpenClaw security stack on the aiops-1 VPS, without ever leaving your Mac.

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
| `apply` | `cd /opt/ai-ops-runner && ./ops/openclaw_apply_remote.sh \|\| true` |
| `guard` | `cd /opt/ai-ops-runner && sudo ./ops/openclaw_install_guard.sh` |
| `ports` | `ss -lntp` |
| `timer` | `systemctl status openclaw-guard.timer --no-pager` |
| `journal` | `journalctl -u openclaw-guard.service -n 200 --no-pager` |
| `artifacts` | `ls -1dt artifacts/* ... && du -sh artifacts/*` |

## Security Model

- **Localhost only**: The server binds to `127.0.0.1:8787`. It is never exposed on `0.0.0.0`.
- **Tailscale CGNAT validation**: `AIOPS_HOST` must be in `100.64.0.0/10`. Anything outside this range is rejected.
- **No arbitrary commands**: Only the above allowlisted operations are permitted. The backend rejects anything not in the list.
- **SSH BatchMode**: All connections use `BatchMode=yes` — no interactive prompts.
- **No secrets in code**: The `.env.local` file (with the Tailscale IP) is gitignored.

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

## Prerequisites

- **Node.js** ≥ 18
- **Tailscale** running and connected (so SSH can reach aiops-1)
- **SSH key** configured for `root@<AIOPS_HOST>` (Tailscale SSH or standard key)

## Install as App Window

For a native app-like experience on macOS, open Safari, navigate to `http://127.0.0.1:8787`, then:

1. **Safari**: File → Add to Dock (macOS Sonoma+)
2. **Chrome**: ⋮ → More Tools → Create Shortcut → "Open as window"

This gives you a standalone window without browser chrome.

## Troubleshooting

**"SSH Connection Failed"**:
- Ensure Tailscale is running: `tailscale status`
- Test manually: `ssh root@100.123.61.57 'echo ok'`
- Check that your SSH key is configured for the target host

**"AIOPS_HOST is not in Tailscale range"**:
- Verify your `.env.local` contains a valid Tailscale IP (100.64.x.x – 100.127.x.x)

**Port 8787 in use**:
- Kill the existing process: `lsof -ti:8787 | xargs kill -9`
- Or change the port in `package.json` scripts and the launcher
