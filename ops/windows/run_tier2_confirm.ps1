# Tier-2 confirmation entrypoint for Windows.
# Invoked by openclaw-nt8-hostd. Sets BACKTEST_ONLY and runs the Python harness.
# Exit codes: 0=ok, 1=gate/validation, 2=usage, 3=NT8_AUTOMATION_NOT_IMPLEMENTED (Phase-0 stub).
param(
    [Parameter(Mandatory = $true)]
    [string]$TopkPath,
    [Parameter(Mandatory = $true)]
    [string]$OutputDir,
    [Parameter(Mandatory = $false)]
    [ValidateSet('strategy_analyzer', 'walk_forward')]
    [string]$Mode = 'strategy_analyzer',
    [Parameter(Mandatory = $false)]
    [string]$Ref
)

$ErrorActionPreference = 'Stop'
$env:BACKTEST_ONLY = 'true'

$repoRoot = if ($env:OPENCLAW_REPO_ROOT) { $env:OPENCLAW_REPO_ROOT } else { Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) }
if (-not (Test-Path -LiteralPath $TopkPath)) {
    Write-Error "Topk file not found: $TopkPath"
    exit 2
}
if (-not (Test-Path -LiteralPath $OutputDir -PathType Container)) {
    New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
}

$python = $null
foreach ($name in @('python', 'python3', 'py')) {
    $c = Get-Command $name -ErrorAction SilentlyContinue
    if ($c) { $python = $c.Source; break }
}
if (-not $python) {
    Write-Error 'Python not found (python, python3, or py). Install Python and ensure it is on PATH.'
    exit 2
}

Set-Location -LiteralPath $repoRoot
& $python -m tools.tier2_confirm_entrypoint --topk $TopkPath --output-dir $OutputDir --mode $Mode
exit $LASTEXITCODE
