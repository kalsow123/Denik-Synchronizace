# Agent instructions — Denik-Synchronizace

## Testovací okno (POVINNÉ)

**POVINNÉ TESTOVACÍ OKNO:** Veškeré testování, backtesty, re-baseline, baseline čísla i jakákoli validace běží **VŽDY** na 2letém okně = poslední 2 roky dat (`BACKTEST_WINDOW_YEARS = 2`; ~2024-05-20 → 2026-05-18, ~24 775 barů EURUSD M30). **NIKDY** ne full-history a **NIKDY** ne jiné okno (např. dřívější 6měsíční 2025-11-10..2026-05-09), pokud to uživatel **VÝSLOVNĚ** nezmění. Platí pro backtest i live a pro všechny budoucí kroky/akce.

Podrobný plán: `VARIANTA A.txt` (kritický blok hned pod hlavičkou).
