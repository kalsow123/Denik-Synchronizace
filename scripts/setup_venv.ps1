# Znovu vytvori .venv v koreni repozitare a nainstaluje zavislosti z requirements.txt.
# Pouzijte po zkopirovani projektu z jineho PC (pyvenv.cfg ukazoval na cizi Python).
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root

$venvDir = Join-Path $Root ".venv"
if (Test-Path -LiteralPath $venvDir) {
    Write-Host "Odstranuji stary .venv ..."
    Remove-Item -LiteralPath $venvDir -Recurse -Force
}

$created = $false
if (Get-Command py -ErrorAction SilentlyContinue) {
    foreach ($launcherArgs in @(@("-3.14"), @("-3.12"), @("-3"))) {
        & py @launcherArgs -m venv .venv
        if ($LASTEXITCODE -eq 0 -and (Test-Path -LiteralPath (Join-Path $Root ".venv\Scripts\python.exe"))) {
            $created = $true
            break
        }
        if (Test-Path -LiteralPath $venvDir) {
            Remove-Item -LiteralPath $venvDir -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}
if (-not $created) {
    $pyexe = (Get-Command python -ErrorAction Stop).Source
    & $pyexe -m venv .venv
}

$venvPy = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPy)) { throw "Venv se nevytvoril: $venvPy" }

& $venvPy -m pip install --upgrade pip
& $venvPy -m pip install -r (Join-Path $Root "requirements.txt")
Write-Host "Hotovo. Python: $venvPy"
