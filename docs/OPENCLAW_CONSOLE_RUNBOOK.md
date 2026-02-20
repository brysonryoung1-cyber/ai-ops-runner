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

### Apply run failed (exit_code 255): get hostd stderr

Apply runs execute on the host (hostd); logs are under `artifacts/hostd/<run_id>/`. The Runs UI now shows **Host executor logs** and **Error** (stderr) for apply runs. To fetch the real failure from the API (e.g. from a machine on Tailscale with token):

```bash
# Set base URL and token (Tailscale; use your token)
BASE="https://aiops-1.tailc75c62.ts.net"
TOKEN="your-X-OpenClaw-Token"

# 1) Get run; response includes artifact_dir (resolved by timestamp if missing)
RUN_ID="20260220011238-3129"
curl -sS -H "X-OpenClaw-Token: $TOKEN" "$BASE/api/runs?id=$RUN_ID" | jq .

# 2) Read stderr from artifact_dir (use path without "artifacts/" prefix)
# From step 1, run.artifact_dir is e.g. "artifacts/hostd/20260220_011238_abc1"
# Path for browse: hostd/20260220_011238_abc1/stderr.txt
ARTIFACT_PATH="hostd/20260220_011238_abc1/stderr.txt"   # replace with actual dir from step 1
curl -sS -H "X-OpenClaw-Token: $TOKEN" "$BASE/api/artifacts/browse?path=$(echo -n "$ARTIFACT_PATH" | jq -sRr @uri)" | jq -r '.content // .error'
```

Common causes of 255: SSH from the host running hostd to the VPS failed (no key, host key changed, or wrong host). Set `OPENCLAW_VPS_SSH_IDENTITY` to a deploy key path (readable by the hostd user) and re-run Apply if needed.

### Fix Apply SSH (deploy key one-time setup)

Apply runs on the **ship host** (the machine where hostd runs); it SSHs to the target (default `root@100.123.61.57`). To fix "Permission denied (publickey,password)":

1. **On the ship host** (e.g. the VPS where HQ/hostd run, or the box that has Tailscale reachability to the target), run once:

   ```bash
   cd /opt/ai-ops-runner   # or your repo path
   sudo ./ops/openclaw_apply_remote_setup_ssh.sh
   ```

   Default target is `root@100.123.61.57`. Override: `sudo ./ops/openclaw_apply_remote_setup_ssh.sh root@other-host`.

2. The script will:
   - Create a dedicated deploy key at `/etc/ai-ops-runner/secrets/openclaw_ssh/vps_deploy_ed25519` (root-only).
   - Try to install the public key on the target via SSH or Tailscale SSH. If it has no access, it **stops** and prints:
     - The public key (safe to share), and
     - The exact one-liner to run **on the target host** (e.g. via console) to add the key to `~/.ssh/authorized_keys`.
   - After the key is on the target: write `/etc/ai-ops-runner/secrets/openclaw_hostd.env` with `OPENCLAW_VPS_SSH_IDENTITY` and `OPENCLAW_VPS_SSH_HOST`, ensure hostd’s systemd unit uses this env file, restart `openclaw-hostd`, and run an SSH proof command. When the proof prints `OK_FROM_DEPLOY_KEY`, setup is done.

3. **Where identity is set**: hostd reads `EnvironmentFile=-/etc/ai-ops-runner/secrets/openclaw_hostd.env` (installed by `ops/install_openclaw_hostd.sh`). That file must contain `OPENCLAW_VPS_SSH_IDENTITY=/etc/ai-ops-runner/secrets/openclaw_ssh/vps_deploy_ed25519` (and optionally `OPENCLAW_VPS_SSH_HOST=root@100.123.61.57`).

4. **Next**: In HQ click **Actions → Apply OpenClaw (Remote)** once. Then `/api/autopilot/status` should be 200 and `/api/ui/health_public` should show a real `build_sha` (not `unknown`).

**Security**: The script never prints the private key. Do not open extra ports or disable Tailscale-only assumptions.

### One-pass Apply fix (copy-paste and deliverables)

Run these **on the ship host** (the machine where hostd runs), then in HQ, then verify.

**Phase 1 — Ship host confirm (paste into ship host shell):**
```bash
hostname
cd /opt/ai-ops-runner && git rev-parse --short HEAD
systemctl status openclaw-hostd --no-pager || true
ssh -o BatchMode=yes -o ConnectTimeout=10 root@100.123.61.57 "echo ok" ; echo "exit=$?"
# Expect: Permission denied + exit=255
```

**Phase 2 — One-time SSH setup on ship host:**
```bash
cd /opt/ai-ops-runner
sudo ./ops/openclaw_apply_remote_setup_ssh.sh root@100.123.61.57
```
- Success: script prints `OK_FROM_DEPLOY_KEY` and completes.
- If script stops at Phase 2 (no access): it prints the **public key** and a **one-liner**. Run that one-liner **on the target** (e.g. from your Mac: `ssh root@100.123.61.57 'mkdir -p /root/.ssh && chmod 700 /root/.ssh && echo "PASTE_PUB_KEY" >> /root/.ssh/authorized_keys && chmod 600 /root/.ssh/authorized_keys'`), then re-run the setup script on the ship host.

**Phase 3 — HQ:** Left nav → Actions → **Apply OpenClaw (Remote)** → Execute. Then Runs → open newest `infra_openclaw` apply run → must be `status=success` with artifact paths.

**Phase 4 — Verify endpoints:** From any machine with HQ reachability (e.g. Mac):
```bash
cd /path/to/ai-ops-runner
OPENCLAW_HQ_BASE="https://YOUR-HQ.tailnet.ts.net" OPENCLAW_HQ_TOKEN="your-token" ./ops/verify_hq_after_apply.sh
```
Requires `curl` and `jq`. Or in browser: `GET /api/ui/health_public` and `GET /api/autopilot/status` (expect 200, build_sha ≠ "unknown").

**Deliverables to post back:**
- Ship host hostname
- Line showing SSH proof `OK_FROM_DEPLOY_KEY`
- Confirmation that `/etc/ai-ops-runner/secrets/openclaw_hostd.env` exists and contains `OPENCLAW_VPS_SSH_IDENTITY` + `OPENCLAW_VPS_SSH_HOST` (do not paste private key)
- Apply run_id + final status
- health_public build_sha value
- autopilot/status HTTP code + key fields (installed, enabled)

### SSH connection failed
```bash
# Ensure Tailscale is running
tailscale status

# Check target configuration
python3 ops/openclaw_targets.py show

# Test SSH directly
ssh root@100.123.61.57 'echo ok'
```
