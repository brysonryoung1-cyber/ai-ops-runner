# Uninstall openclaw-nt8-hostd Windows Service.
param(
    [Parameter(Mandatory = $false)]
    [string]$ServiceName = 'openclaw-nt8-hostd'
)

$ErrorActionPreference = 'Stop'
$svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if (-not $svc) {
    Write-Host "Service $ServiceName is not installed."
    exit 0
}
Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
& sc.exe delete $ServiceName
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to delete service $ServiceName. Run as Administrator."
    exit 1
}
Write-Host "Uninstalled $ServiceName."
