"""Sanity-check: relaxed_wave_box_enabled=[True] v KAZDE grid-kombinaci PROFILES.

Jednorazovy overovaci skript pro zapnuti "profilu B" v backtest/grid/backtest_conf.py.
Neni soucasti test suite (pytest) — spoustet manualne po editaci PROFILES.
"""
import backtest.grid.backtest_conf as m

checked = 0
missing = []

for profile_name, profile in m.PROFILES.items():
    grid = profile.get("grid")
    if not isinstance(grid, list):
        continue  # napr. "bot_finish" nema "grid" (explicit_combos_file + base) - mimo scope
    for idx, combo in enumerate(grid):
        if not isinstance(combo, dict):
            continue
        checked += 1
        val = combo.get("relaxed_wave_box_enabled")
        if val != [True]:
            missing.append((profile_name, idx, val))

print(f"Zkontrolovano kombinaci: {checked}")
print(f"Chybejici/spatne: {len(missing)}")
for profile_name, idx, val in missing:
    print(f"  PROFILES[{profile_name!r}][\"grid\"][{idx}] -> relaxed_wave_box_enabled={val!r}")

if missing:
    raise SystemExit(1)
