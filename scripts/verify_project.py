"""
Rychla hromadna kontrola: syntaxe, importy, konzistence config/grid, min. backtest.
Spustit z korene projektu:  py -3 scripts/verify_project.py
"""
from __future__ import annotations

import compileall
import importlib
import sys
from datetime import datetime, timedelta
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))

    errs: list[str] = []

    # 0) Kořenové skripty
    import py_compile

    for name in ("main.py", "download_data.py"):
        fp = root / name
        if fp.is_file():
            try:
                py_compile.compile(str(fp), doraise=True)
            except py_compile.PyCompileError as e:
                errs.append(f"compile {name}: {e}")

    # 1) compileall — projektove moduly (bez .venv)
    skip = {".venv", "__pycache__", ".git"}
    ok_compile = True
    for sub in ("config", "core", "strategy", "infra", "runtime", "backtest", "scripts"):
        p = root / sub
        if not p.is_dir():
            continue
        r = compileall.compile_dir(str(p), quiet=1)
        if not r:
            ok_compile = False
    if not ok_compile:
        errs.append("compileall: nektere soubory nesel prelozit (viz vystup vyse)")

    mods = [
        "config.bot_config",
        "config.enums",
        "core.signal_keys",
        "core.risk",
        "strategy.wave_detection",
        "strategy.pine_recovery",
        "infra.orders",
        "runtime.startup",
        "runtime.live_loop",
        "backtest.engine",
        "backtest.grid.backtest_conf",
        "backtest.grid.translator",
        "backtest.run_backtest",
        "main",
    ]
    for m in mods:
        try:
            importlib.import_module(m)
        except Exception as e:
            errs.append(f"import {m}: {e}")

    if errs:
        print("CHYBY:")
        for e in errs:
            print(" ", e)
        return 1

    # 2) BotConfig default: entry fib < sl fib
    from config.bot_config import BotConfig

    d = BotConfig()
    if not (d.entry_fib_level < d.sl_fib_level):
        errs.append(f"BotConfig default: entry_fib {d.entry_fib_level} >= sl_fib {d.sl_fib_level}")

    # 3) Enum vs backtest engine rezimy
    from config.enums import EntryMode
    from backtest.engine import _entry_mode_str

    cfg = BotConfig()
    for mode in (
        EntryMode.MARKET_FALLBACK,
        EntryMode.STOP_FALLBACK,
        EntryMode.NO_FALLBACK,
        EntryMode.LIMIT_FALLBACK,
    ):
        cfg.entry_mode = mode
        s = _entry_mode_str(cfg)
        if not s:
            errs.append(f"_entry_mode_str prazdny pro {mode}")

    # 4) Grid: entry_fib < sl_fib po translatoru
    from backtest.grid.backtest_conf import PROFILES, generate_combinations
    from backtest.grid.translator import grid_dict_to_bot_config

    for pname, profile in PROFILES.items():
        for combo in generate_combinations(profile):
            c = grid_dict_to_bot_config(combo)
            if not (float(c.entry_fib_level) < float(c.sl_fib_level)):
                errs.append(f"profil {pname}: fib {c.entry_fib_level} >= sl_fib {c.sl_fib_level}")

    # 5) Minibaleni backtestu + wave vs pine fib50/sl/tp
    import pandas as pd
    from backtest.engine import BacktestEngine
    from strategy.wave_detection import detect_waves
    from strategy.pine_recovery import simulate_pine_pending_state
    from config.enums import EntryMode

    t0 = datetime(2026, 1, 1, 12, 0)
    rows = []
    price = 100.0
    for i in range(12):
        o, c = price, price + 0.05
        rows.append(
            dict(
                time=t0 + timedelta(minutes=30 * i),
                open=o,
                high=c + 0.01,
                low=o - 0.01,
                close=c,
            )
        )
        price = c
    for j in range(3):
        i = 12 + j
        o, c = price, price - 0.05
        rows.append(
            dict(
                time=t0 + timedelta(minutes=30 * i),
                open=o,
                high=o + 0.01,
                low=c - 0.01,
                close=c,
            )
        )
        price = c
    df = pd.DataFrame(rows)

    bt_cfg = BotConfig(
        wave_min_pct=0.45,
        min_opp_bars=2,
        entry_fib_level=0.5,
        sl_fib_level=0.8,
        rrr=2.0,
        entry_mode=EntryMode.MARKET_FALLBACK,
        wave_session_filter_enabled=False,
        session_enabled=False,
    )
    waves = detect_waves(df, bt_cfg)
    if not waves:
        errs.append("sanity: detect_waves nevratil vlnu na syntetickem ramci")
    else:
        w0 = waves[0]
        if w0["dir"] != 1:
            errs.append(f"sanity: UP vlna ocekavano dir=1, je {w0['dir']}")
        if not (w0["sl"] < w0["fib50"] < w0["tp"]):
            errs.append(f"sanity: BUY geometrie sl<fib<tp: {w0['sl']}, {w0['fib50']}, {w0['tp']}")

    pend, _ = simulate_pine_pending_state(df, bt_cfg)
    if waves and pend:
        a, b = waves[0], pend[0]
        for k in ("fib50", "sl", "tp", "dir"):
            if abs(float(a[k]) - float(b[k])) > 1e-9:
                errs.append(f"sanity: wave vs pine nezhodne {k}: {a[k]} vs {b[k]}")

    eng = BacktestEngine(bt_cfg, backtest_spread=0.01, backtest_slippage=0.0)
    _ = eng.run(df)
    if eng.wave_debug.get("waves_detected_total", 0) < 1:
        errs.append("BacktestEngine: waves_detected_total == 0")

    if errs:
        print("CHYBY (logika):")
        for e in errs:
            print(" ", e)
        return 1

    print("OK — verify_project.py")
    print("  compileall: config, core, strategy, infra, runtime, backtest, scripts")
    print(f"  importy: {len(mods)} modulu (vetne main + run_backtest)")
    print(f"  grid profilu: {len(PROFILES)}, vsechny kombinace entry_fib < sl_fib")
    print("  sanity: detect_waves + pine_recovery + BacktestEngine.run")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
