# Install openclaw-nt8-hostd as a Windows Service (or scheduled task).
# Requires: .NET 8 SDK for build. Run from repo root or set OPENCLAW_REPO_ROOT.
# Sets OPENCLAW_NT8_HOSTD_TOKEN and BACKTEST_ONLY=true for the service.
param(
    [Parameter(Mandatory = $false)]
    [string]$RepoRoot = $env:OPENCLAW_REPO_ROOT,
    [Parameter(Mandatory = $false)]
    [string]$Token,
    [Parameter(Mandatory = $false)]
    [int]$Port = 8878
)

$ErrorActionPreference = 'Stop'
if (-not $RepoRoot) { $RepoRoot = (Get-Location).Path }
$RepoRoot = [System.IO.Path]::GetFullPath($RepoRoot)
$hostdProj = Join-Path $RepoRoot 'tools\nt8_hostd\nt8_hostd.csproj'
if (-not (Test-Path $hostdProj)) {
    Write-Error "Repo root not found (nt8_hostd project missing): $RepoRoot"
    exit 1
}

# Build: win-x64 single-file
$publishDir = Join-Path $RepoRoot 'artifacts\nt8_hostd\publish'
& dotnet publish $hostdProj -c Release -r win-x64 --self-contained true -p:PublishSingleFile=true -o $publishDir
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$exePath = Join-Path $publishDir 'openclaw-nt8-hostd.exe'
if (-not (Test-Path $exePath)) {
    Write-Error "Publish did not produce executable: $exePath"
    exit 1
}

# Token: require for install (never log full value). Reject CMD metacharacters to avoid injection.
if (-not $Token) { $Token = $env:OPENCLAW_NT8_HOSTD_TOKEN }
if (-not $Token) {
    Write-Error 'Set OPENCLAW_NT8_HOSTD_TOKEN (or pass -Token). Required for service auth.'
    exit 1
}
$unsafe = [regex]::Match($Token, '[\x00\r\n&|<>%^]')
if ($unsafe.Success) {
    Write-Error 'Token must not contain CMD metacharacters or newlines (e.g. & | < > % ^). Use an alphanumeric secret.'
    exit 1
}
# For batch: escape % as %%
$tokenForBatch = $Token -replace '%', '%%'
# RepoRoot is also written into the batch file; reject CMD metacharacters to avoid injection.
$repoRootUnsafe = [regex]::Match($RepoRoot, '[\x00\r\n&|<>%^]')
if ($repoRootUnsafe.Success) {
    Write-Error 'OPENCLAW_REPO_ROOT (or -RepoRoot) must not contain CMD metacharacters or newlines (e.g. & | < > % ^).'
    exit 1
}
$repoRootForBatch = $RepoRoot -replace '%', '%%'

# Install as Windows Service via sc.exe (or NSSM if preferred). Using sc.exe.
$svcName = 'openclaw-nt8-hostd'
$displayName = 'OpenClaw NT8 Tier-2 Host Executor'
$binPath = "`"$exePath`""
# Environment: set in registry for the service (LocalSystem) or use a wrapper that sets env and runs exe.
# sc create does not set env; we use a wrapper script or store env in a secure location and run via cmd.
$envFile = Join-Path (Join-Path $RepoRoot 'artifacts\nt8_hostd') 'hostd.env'
$envDir = [System.IO.Path]::GetDirectoryName($envFile)
if (-not (Test-Path $envDir)) { New-Item -ItemType Directory -Path $envDir -Force | Out-Null }
# Store token in a file that only admins can read; service will read it at startup if we implement file-based token.
# Simpler: pass env via a batch wrapper so the service binary sees OPENCLAW_NT8_HOSTD_TOKEN and BACKTEST_ONLY.
$wrapperBat = Join-Path $publishDir 'run_nt8_hostd.bat'
@"
@echo off
set OPENCLAW_NT8_HOSTD_TOKEN=$tokenForBatch
set BACKTEST_ONLY=true
set OPENCLAW_REPO_ROOT=$repoRootForBatch
set NT8_HOSTD_PORT=$Port
"$exePath"
"@ | Set-Content -Path $wrapperBat -Encoding ASCII
# Restrict ACL so only SYSTEM and local Administrators can read (token is in file). Fail install if this fails.
try {
    $acl = Get-Acl -Path $wrapperBat
    $acl.SetAccessRuleProtection($true, $false)
    $acl.Access | ForEach-Object { $acl.RemoveAccessRule($_) | Out-Null }
    $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule('SYSTEM', 'FullControl', 'Allow')))
    $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule('BUILTIN\Administrators', 'FullControl', 'Allow')))
    Set-Acl -Path $wrapperBat -AclObject $acl
} catch {
    Write-Error "ACL hardening failed on $wrapperBat. Install aborted to avoid leaving token readable. Error: $_"
    exit 1
}
# SCM requires an executable; run the batch via cmd.exe so the service binary is valid.
$binPath = "cmd.exe /c `"$wrapperBat`""

$existing = Get-Service -Name $svcName -ErrorAction SilentlyContinue
if ($existing) {
    Stop-Service -Name $svcName -Force -ErrorAction SilentlyContinue
    & sc.exe delete $svcName
    Start-Sleep -Seconds 2
}
& sc.exe create $svcName binPath= $binPath start= auto DisplayName= $displayName
if ($LASTEXITCODE -ne 0) {
    Write-Error 'Failed to create service. Run as Administrator.'
    exit 1
}
Start-Service -Name $svcName
Write-Host "Service $svcName installed and started. Listening on 127.0.0.1:$Port"
Write-Host "Token is set in the wrapper batch file; restrict permissions on that file if needed."
