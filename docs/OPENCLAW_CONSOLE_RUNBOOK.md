# OpenClaw Console — Production Runbook

## Quick Reference

| Task | Command |
|------|---------|
| Build | `./ops/openclaw_console_build.sh` |
| Start | `./ops/openclaw_console_start.sh` |
| Status | `./ops/openclaw_console_status.sh` |
| Stop | `./ops/openclaw_console_stop.sh` |
| Install autostart | `./ops/openclaw_console_install_macos_launchagent.sh` |
| Uninstall autostart | `./ops/openclaw_console_uninstall_macos_launchagent.sh` |
| Token rotate | `python3 ops/openclaw_console_token.py rotate` |
| Token status | `python3 ops/openclaw_console_token.py status` |
| Init targets | `python3 ops/openclaw_targets.py init` |
| Show targets | `python3 ops/openclaw_targets.py show` |
| Set active target | `python3 ops/openclaw_targets.py set-active <name>` |

## First-Time Setup

```bash
# 1. Initialize target configuration
python3 ops/openclaw_targets.py init

# 2. Generate auth token (stored in macOS Keychain)
python3 ops/openclaw_console_token.py rotate

# 3. Build for production
./ops/openclaw_console_build.sh

# 4. Start
./ops/openclaw_console_start.sh

# 5. Verify
curl -sSf http://127.0.0.1:8787

# 6. (Optional) Install autostart at login
./ops/openclaw_console_install_macos_launchagent.sh
```

## Build

```bash
./ops/openclaw_console_build.sh
```

- Checks Node.js >= 18 and npm
- Creates `.env.local` from `.env.example` if missing
- Runs `npm ci` + `next build`
- Prints "OK: build complete" on success

## Start / Stop / Status

```bash
# Start (production, background, 127.0.0.1 only)
./ops/openclaw_console_start.sh

# Check status (PID, URL, last 30 log lines)
./ops/openclaw_console_status.sh

# Stop (graceful shutdown, fallback force-kill)
./ops/openclaw_console_stop.sh
```

**Idempotency**: `start` exits 0 if already running. `stop` exits 0 if already stopped.

**Port**: Default `8787`. Override with `OPENCLAW_CONSOLE_PORT=9090`.

**Logs**: Written to `logs/openclaw_console/<timestamp>/server.log`. A symlink at `logs/openclaw_console/latest/` always points to the most recent run.

## Auth Token

The console API requires an `X-OpenClaw-Token` header when a token is configured.

```bash
# Check token status (shows masked fingerprint only)
python3 ops/openclaw_console_token.py status

# Rotate (generate new token, store in Keychain)
python3 ops/openclaw_console_token.py rotate
```

**Storage**: macOS Keychain, service=`ai-ops-runner`, account=`OPENCLAW_CONSOLE_TOKEN`.

**Flow**: `start.sh` reads the token from Keychain and passes it as `OPENCLAW_CONSOLE_TOKEN` env var to the Next.js process. The middleware checks it on all `/api/*` requests.

**No token**: If no token is configured, API routes work without auth (origin validation still active).

## Tailscale Targets

Targets define which remote server the console manages.

```bash
# Initialize default targets file
python3 ops/openclaw_targets.py init

# Show all targets
python3 ops/openclaw_targets.py show

# Switch active target
python3 ops/openclaw_targets.py set-active aiops-1
```

**File**: `~/.config/openclaw/targets.json`

**Validation**: Host IPs must be in `100.64.0.0/10` (Tailscale CGNAT). Non-tailnet IPs are rejected fail-closed.

**Users**: Must be `root` or `runner`.

## macOS LaunchAgent (Autostart)

```bash
# Install (runs console at login)
./ops/openclaw_console_install_macos_launchagent.sh

# Uninstall
./ops/openclaw_console_uninstall_macos_launchagent.sh

# Check
launchctl list | grep openclaw
```

**Plist**: `~/Library/LaunchAgents/com.openclaw.console.plist`

## Security Model

1. **Localhost only**: Server binds to `127.0.0.1` — never `0.0.0.0`.
2. **Token auth**: All `/api/*` routes require `X-OpenClaw-Token` header (when configured).
3. **CSRF/origin validation**: Only same-origin requests from `127.0.0.1` / `localhost` accepted.
4. **CORS**: No permissive headers — cross-origin requests blocked by default.
5. **Command allowlist**: Only 7 predefined SSH commands can execute. No arbitrary execution.
6. **Tailscale CGNAT**: Target hosts must be in `100.64.0.0/10`. Non-tailnet IPs rejected.
7. **No secrets in logs**: Security events log path/status only, never tokens or credentials.

## Troubleshooting

### Console won't start
```bash
# Check for stale PID
cat logs/openclaw_console/console.pid
kill $(cat logs/openclaw_console/console.pid) 2>/dev/null; rm -f logs/openclaw_console/console.pid

# Check logs
tail -50 logs/openclaw_console/latest/server.log

# Rebuild
./ops/openclaw_console_build.sh
./ops/openclaw_console_start.sh
```

### API returns 401
```bash
# Token may be missing or mismatched
python3 ops/openclaw_console_token.py status

# Rotate and restart
python3 ops/openclaw_console_token.py rotate
./ops/openclaw_console_stop.sh
./ops/openclaw_console_start.sh
```

### SSH connection failed
```bash
# Ensure Tailscale is running
tailscale status

# Check target configuration
python3 ops/openclaw_targets.py show

# Test SSH directly
ssh root@100.123.61.57 'echo ok'
```
