# Spusti grid EXAMPLE se stejnymi prepinaci jako pri rucnim testu — vzdy z korene teto kopie projektu.
# Spusteni: z libovolne slozky  PS>  cesta\k\repo\scripts\run_grid_EXAMPLE.ps1
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root

$python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    Write-Error "Chybi .venv. Spuste nejdriv: .\scripts\setup_venv.ps1"
}

& $python -m backtest.run_backtest `
    --profile grid `
    --grid-profile EXAMPLE `
    --plot `
    --visual-waves `
    --visual-html `
    --visual-full-span `
    --plot-trades `
    --plot-trades-html `
    --plot-monthly-kind-html `
    --plot-scroll-combined-html `
    --output results
