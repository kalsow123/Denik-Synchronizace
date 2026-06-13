param(
    [string]$EnvFile = ".env.sync"
)

$ErrorActionPreference = "Stop"

$python = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Error "Nenalezen Python ve virtual env: $python"
}

& $python "scripts/sync_live_jsonl_to_github.py" --env-file $EnvFile
