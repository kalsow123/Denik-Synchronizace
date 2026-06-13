# Grid best_candidates + P.A. diagnosticky HTML (jako diagnostika_pa_kumulativni_pnl_ALL.html).
# Pred prvnim behrem: runtime/adx14_normalizer.json (fit ze stejneho CSV jako backtest).
#
# Pokud PowerShell hlasi "running scripts is disabled", pouzijte misto toho:
#   scripts\run_grid_best_candidates_adx14.cmd
# nebo:
#   powershell -ExecutionPolicy Bypass -File scripts\run_grid_best_candidates_adx14.ps1
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root

$python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    Write-Error "Chybi .venv. Spuste nejdriv: .\scripts\setup_venv.ps1"
}

& $python -m backtest.run_backtest `
  --profile grid `
  --grid-profile bot_optimalisation `
  --plot `
  --plot-top-n 4 `
  --plot-adx14 `
  --plots-html-only `
  --output results
