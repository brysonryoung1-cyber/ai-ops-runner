# Tier-2 confirmation harness smoke: hostd + fixture topk -> done.json + artifacts.
# Runs hostd locally, submits fixture topk, waits for done.json, verifies tier2 artifacts exist.
# Use this script name for Tier-2 contract verification. See also nt8_hostd_smoke.ps1.
# Requires: .NET 8, pwsh, Python. Best run on Windows.
param(
    [Parameter(Mandatory = $false)]
    [string]$RepoRoot = $env:OPENCLAW_REPO_ROOT,
    [Parameter(Mandatory = $false)]
    [int]$Port = 18999
)

$ErrorActionPreference = 'Stop'
if (-not $RepoRoot) { $RepoRoot = (Get-Location).Path }
$RepoRoot = [System.IO.Path]::GetFullPath($RepoRoot)
$proj = Join-Path $RepoRoot 'tools\nt8_hostd\nt8_hostd.csproj'
$fixtureTopk = Join-Path $RepoRoot 'tools\tests\fixtures\sample_topk.json'
if (-not (Test-Path $proj)) { Write-Error "Repo root invalid (no nt8_hostd project): $RepoRoot"; exit 1 }
if (-not (Test-Path $fixtureTopk)) { Write-Error "Fixture topk not found: $fixtureTopk"; exit 1 }

$token = [System.Guid]::NewGuid().ToString('N')
$env:OPENCLAW_NT8_HOSTD_TOKEN = $token
$env:BACKTEST_ONLY = 'true'
$env:OPENCLAW_REPO_ROOT = $RepoRoot
$env:NT8_HOSTD_PORT = $Port.ToString()
$baseUrl = "http://127.0.0.1:$Port"
$outputRoot = Join-Path $RepoRoot 'artifacts\tier2_hostd_smoke'
if (Test-Path $outputRoot) { Remove-Item -Recurse -Force $outputRoot }
New-Item -ItemType Directory -Path $outputRoot -Force | Out-Null

# Build hostd once
& dotnet build (Join-Path $RepoRoot 'tools\nt8_hostd\nt8_hostd.csproj') -c Release -v q
if ($LASTEXITCODE -ne 0) { Write-Error 'dotnet build failed'; exit 1 }

# Start hostd in background
$hostdJob = Start-Job -ScriptBlock {
    param($r, $p)
    $env:OPENCLAW_REPO_ROOT = $r
    $env:NT8_HOSTD_PORT = $p
    $env:OPENCLAW_NT8_HOSTD_TOKEN = $using:token
    $env:BACKTEST_ONLY = 'true'
    Set-Location $r
    & dotnet run --project (Join-Path $r 'tools\nt8_hostd\nt8_hostd.csproj') -c Release --no-build 2>&1
} -ArgumentList $RepoRoot, $Port
Start-Sleep -Seconds 5
$health = try { Invoke-RestMethod -Uri "$baseUrl/v1/orb/backtest/confirm_nt8/health" -Headers @{ Authorization = "Bearer $token" } -Method Get } catch { $null }
if (-not $health -or -not $health.ok) {
    Receive-Job $hostdJob
    Stop-Job $hostdJob; Remove-Job $hostdJob
    Write-Error 'hostd did not become ready'
    exit 1
}

try {
    $topkJson = Get-Content -Raw -LiteralPath $fixtureTopk
    $body = @{
        topk_inline   = $topkJson
        output_root   = $outputRoot
        mode          = 'strategy_analyzer'
        BACKTEST_ONLY = $true
    } | ConvertTo-Json -Compress
    $runResp = Invoke-RestMethod -Uri "$baseUrl/v1/orb/backtest/confirm_nt8/run" -Method Post -Headers @{ Authorization = "Bearer $token"; 'Content-Type' = 'application/json' } -Body $body
    if (-not $runResp.ok -or -not $runResp.run_id) { Write-Error "run failed: $($runResp | ConvertTo-Json -Compress)"; exit 1 }
    $runId = $runResp.run_id
    Write-Host "Run started: $runId"

    # Poll status until done (or timeout)
    $done = $false
    $status = $null
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 2
        $status = Invoke-RestMethod -Uri "$baseUrl/v1/orb/backtest/confirm_nt8/status?run_id=$runId" -Headers @{ Authorization = "Bearer $token" } -Method Get
        if ($status.state -eq 'done') { $done = $true; break }
    }
    if (-not $done) { Write-Error "Run did not complete in time. status=$($status | ConvertTo-Json -Compress)"; exit 1 }
    if ($status.exit_code -ne 3) { Write-Error "Expected exit_code 3 (Phase-0 stub), got $($status.exit_code)"; exit 1 }

    # Canonical layout: artifact_dir is .../run_id/tier2_nt8, tier2/done.json inside
    $donePath = Join-Path $status.artifact_dir 'tier2\done.json'
    if (-not (Test-Path $donePath)) { Write-Error "done.json not found at $donePath"; exit 1 }
    $summaryPath = Join-Path $status.artifact_dir 'tier2\summary.json'
    $resultsPath = Join-Path $status.artifact_dir 'tier2\results.csv'
    if (-not (Test-Path $summaryPath)) { Write-Error "summary.json not found at $summaryPath"; exit 1 }
    if (-not (Test-Path $resultsPath)) { Write-Error "results.csv not found at $resultsPath"; exit 1 }
    Write-Host "Artifacts verified: done.json, summary.json, results.csv"

    $zipPath = Join-Path $outputRoot 'collect.zip'
    Invoke-WebRequest -Uri "$baseUrl/v1/orb/backtest/confirm_nt8/collect?run_id=$runId" -Headers @{ Authorization = "Bearer $token" } -Method Get -OutFile $zipPath
    if (-not (Test-Path $zipPath) -or (Get-Item $zipPath).Length -eq 0) { Write-Error "Collect zip missing or empty"; exit 1 }
    Write-Host "Collect zip saved: $zipPath"
    Write-Host "tier2_hostd smoke PASS"
}
finally {
    Stop-Job $hostdJob -ErrorAction SilentlyContinue
    Remove-Job $hostdJob -Force -ErrorAction SilentlyContinue
}
