@echo off
REM Grid best_candidates + P.A. diagnosticky HTML (obejde PowerShell Execution Policy).
REM Spusteni: dvojklik nebo z cmd:  scripts\run_grid_best_candidates_adx14.cmd
setlocal
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
    echo Chybi .venv. Nejdriv spustte scripts\setup_venv.ps1 nebo vytvorte venv rucne.
    exit /b 1
)
".venv\Scripts\python.exe" -m backtest.run_backtest ^
  --profile grid ^
  --grid-profile bot_optimalisation ^
  --plot ^
  --plot-top-n 4 ^
  --plot-adx14 ^
  --plots-html-only ^
  --output results
endlocal
