# Tier-2 Windows Run (openclaw-nt8-hostd)

The **openclaw-nt8-hostd** service runs on a Windows machine and exposes a localhost-only API for Tier-2 confirmation jobs (orb.backtest.confirm_nt8). OpenClaw or other callers use it to trigger run/status/collect against the existing PowerShell entrypoint and Python harness.

## Binding and security

- **Bind address**: `127.0.0.1` only. No `0.0.0.0`. The service is not exposed to the network by default.
- **Auth**: Bearer token required. Set `OPENCLAW_NT8_HOSTD_TOKEN` in the environment; clients must send `Authorization: Bearer <token>` on every request. Missing or wrong token → 403. The service logs only a 6-character token fingerprint, never the token itself.
- **Fail-closed**: The service requires `BACKTEST_ONLY=true` in the environment and in the request (or in the inline topk). Otherwise it returns 403 `BACKTEST_ONLY_REQUIRED`.
- **Single-flight**: At most one active run at a time. If a run is already active, `POST run` returns 409 with `active_run_id`.

## Required environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENCLAW_NT8_HOSTD_TOKEN` | Yes | Shared secret for bearer auth. Must be set for the service and for clients. |
| `BACKTEST_ONLY` | Yes | Must be `true` or the service rejects run requests. |
| `OPENCLAW_REPO_ROOT` | Recommended | Repo root (defaults to current directory). Used to resolve `ops/windows/run_tier2_confirm.ps1` and Python module. |
| `NT8_HOSTD_PORT` | No | Port to listen on (default 8878). |

## Endpoints (allowlist)

Base path: `/v1/orb/backtest/confirm_nt8/`

| Method | Path | Description |
|--------|------|-------------|
| POST | `run` | Start a Tier-2 confirmation run. Body: `topk_path` or `topk_inline`, `output_root`, `mode`, optional `force`, `ref`. Returns `run_id`, `artifact_dir`. |
| GET | `status?run_id=<id>` | Get run state: `running` or `done`, `exit_code`, `summary`, `artifact_dir`. |
| GET | `collect?run_id=<id>` | Download a zip of the run’s artifact directory. |
| GET | `health` | Health check; returns `ok`, `token_fingerprint`, `backtest_only_env`. |

### POST run — request body

- **topk_path** (string, optional): Path to `topk.json`. If relative, resolved from repo root.
- **topk_inline** (string or object, optional): Inline topk JSON. If provided, written to `<output_root>/<run_id>/topk.json` and used as input.
- **output_root** (string, required): Root directory for run artifacts. Artifacts are written under `<output_root>/<run_id>/tier2_nt8/tier2/...`; the API returns `artifact_dir` = `<output_root>/<run_id>/tier2_nt8`.
- **mode** (string, optional): `strategy_analyzer` (default) or `walk_forward`.
- **BACKTEST_ONLY** (boolean, required): Must be `true` in the body (or inside `topk_inline`).
- **force**, **ref** (optional): Reserved for future use.

Example (curl):

```bash
export TOKEN="your-openclaw-nt8-hostd-token"
curl -s -X POST "http://127.0.0.1:8878/v1/orb/backtest/confirm_nt8/run" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"topk_path":"C:/path/to/topk.json","output_root":"C:/artifacts","mode":"strategy_analyzer","BACKTEST_ONLY":true}'
```

Example (PowerShell):

```powershell
$token = $env:OPENCLAW_NT8_HOSTD_TOKEN
$topkJson = Get-Content -Raw C:\path\to\topk.json
$body = @{
  topk_inline   = $topkJson
  output_root   = "C:\artifacts"
  mode          = "strategy_analyzer"
  BACKTEST_ONLY = $true
} | ConvertTo-Json -Compress
Invoke-RestMethod -Uri "http://127.0.0.1:8878/v1/orb/backtest/confirm_nt8/run" -Method Post `
  -Headers @{ Authorization = "Bearer $token"; "Content-Type" = "application/json" } -Body $body
```

### GET status

```bash
curl -s "http://127.0.0.1:8878/v1/orb/backtest/confirm_nt8/status?run_id=20260218-12345678-abcdef01" \
  -H "Authorization: Bearer $TOKEN"
```

Response (done): `{"state":"done","exit_code":3,"summary":{...},"artifact_dir":"C:\\artifacts\\20260218-...","status":"NT8_AUTOMATION_NOT_IMPLEMENTED"}`

### GET collect

```bash
curl -s "http://127.0.0.1:8878/v1/orb/backtest/confirm_nt8/collect?run_id=20260218-12345678-abcdef01" \
  -H "Authorization: Bearer $TOKEN" -o tier2-run.zip
```

## Run directory layout and artifacts

- Each run gets `<output_root>/<run_id>/tier2_nt8/` as the **artifact_dir** (returned in run and status). The harness writes `tier2/` inside that:
  - `tier2/done.json`, `tier2/summary.json`, `tier2/results.csv`, `tier2/raw_exports/`, `tier2/logs/runner.log`.
- **done.json** is the completion marker. When present, `state` is `done` and `exit_code` is set (e.g. 3 for Phase-0 stub).
- Hostd state is under `artifacts/nt8_hostd/state.json`. Job queue: `artifacts/nt8_hostd/jobs/<run_id>/` (artifact_dir.txt, confirm_spec.json).

## Installing and running on Windows

1. **Build and install** (run as Administrator from repo root, or set `OPENCLAW_REPO_ROOT`):

   ```powershell
   .\ops\windows\install_nt8_hostd.ps1 -Token $env:OPENCLAW_NT8_HOSTD_TOKEN -Port 8878
   ```

   This builds the .NET 8 service (win-x64 single-file), installs the Windows Service **openclaw-nt8-hostd**, sets the token and `BACKTEST_ONLY=true` via a wrapper, and starts the service.

2. **Uninstall**:

   ```powershell
   .\ops\windows\uninstall_nt8_hostd.ps1
   ```

3. **Verify locally** (after install):

   ```powershell
   $token = $env:OPENCLAW_NT8_HOSTD_TOKEN
   Invoke-RestMethod -Uri "http://127.0.0.1:8878/v1/orb/backtest/confirm_nt8/health" -Headers @{ Authorization = "Bearer $token" }
   ```

## Exposing via Tailscale (optional)

The service binds to 127.0.0.1 only. To reach it from another machine (e.g. a Linux runner), you can use **Tailscale** and serve the hostd port only on the Tailscale interface, or run a small reverse proxy that listens on the Tailscale IP. This doc does not configure Tailscale; it is a note for operators who want remote access without opening the host to 0.0.0.0.

## Verification steps (install + smoke)

1. **Build and install** (run as Administrator from repo root):

   ```powershell
   $env:OPENCLAW_NT8_HOSTD_TOKEN = 'your-secret-token'
   .\ops\windows\install_nt8_hostd.ps1 -Token $env:OPENCLAW_NT8_HOSTD_TOKEN -Port 8878
   ```

2. **Verify health** (same machine):

   ```powershell
   $token = $env:OPENCLAW_NT8_HOSTD_TOKEN
   Invoke-RestMethod -Uri "http://127.0.0.1:8878/v1/orb/backtest/confirm_nt8/health" -Headers @{ Authorization = "Bearer $token" }
   ```

   Expect: `ok: true`, `backtest_only_env: true`.

3. **Run a sample job** (fixture topk):

   ```powershell
   $token = $env:OPENCLAW_NT8_HOSTD_TOKEN
   $topk = Get-Content -Raw (Join-Path $PWD 'tools\tests\fixtures\sample_topk.json')
   $body = @{ topk_inline = $topk; output_root = "C:\artifacts\tier2"; mode = "strategy_analyzer"; BACKTEST_ONLY = $true } | ConvertTo-Json -Compress
   $run = Invoke-RestMethod -Uri "http://127.0.0.1:8878/v1/orb/backtest/confirm_nt8/run" -Method Post -Headers @{ Authorization = "Bearer $token"; "Content-Type" = "application/json" } -Body $body
   # Poll: GET .../status?run_id=$($run.run_id) until state=done; then GET .../collect?run_id=... -OutFile tier2-run.zip
   ```

4. **Smoke test** (starts hostd in-process, no service):

   ```powershell
   .\ops\tests\tier2_hostd_smoke.ps1
   ```
   Or: `.\ops\tests\nt8_hostd_smoke.ps1`

   Both start hostd on a random port, submit fixture topk, wait for `done.json`, and verify `tier2/results.csv`, `summary.json`, and collect zip.
