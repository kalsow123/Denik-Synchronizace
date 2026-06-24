# Bezpecny restart live bota: stop (PID z locku) -> cekani -> start main.py
# Pouziti z korene repa nebo odkudkoli:
#   .\scripts\restart_bot.ps1
param(
    [int]$WaitSeconds = 2
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root

$python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Nenalezen Python ve virtual env: $python"
}

function Get-LockPid {
    param([string]$LockFile)
    if (-not (Test-Path -LiteralPath $LockFile)) {
        return $null
    }
    try {
        $raw = (Get-Content -LiteralPath $LockFile -Raw).Trim()
        if ($raw -match '^\d+$') {
            return [int]$raw
        }
    } catch {
        return $null
    }
    return $null
}

function Stop-LockProcess {
    param(
        [string]$Label,
        [string]$LockFile
    )
    $lockPid = Get-LockPid -LockFile $LockFile
    if ($null -eq $lockPid) {
        if (Test-Path -LiteralPath $LockFile) {
            Remove-Item -LiteralPath $LockFile -Force
            Write-Host "${Label}: smazan zastaraly lock (bez PID)."
        }
        return
    }
    $proc = Get-Process -Id $lockPid -ErrorAction SilentlyContinue
    if ($proc) {
        Write-Host "${Label}: ukoncuji PID $lockPid ..."
        Stop-Process -Id $lockPid -Force
    } else {
        Write-Host "${Label}: PID $lockPid nebezi, cistim lock."
    }
    Remove-Item -LiteralPath $LockFile -Force -ErrorAction SilentlyContinue
}

$lockPath = & $python -c @"
from config.bot_config import LIVE_BOT_CONFIG
from runtime.instance_lock import LiveInstanceLock
print(LiveInstanceLock(LIVE_BOT_CONFIG)._path.resolve())
"@

if (-not $lockPath) {
    throw "Nepodarilo se zjistit cestu k lock souboru live bota."
}

$telemetryLock = Join-Path $Root "locks\telemetry_sync.lock"

Write-Host "Restart live bota v: $Root"
Stop-LockProcess -Label "Live bot" -LockFile $lockPath
Stop-LockProcess -Label "Telemetry sync" -LockFile $telemetryLock

if ($WaitSeconds -gt 0) {
    Write-Host "Cekam ${WaitSeconds}s ..."
    Start-Sleep -Seconds $WaitSeconds
}

Write-Host "Spoustim: python -u -m main"
& $python -u -m main
