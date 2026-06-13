"""Diagnostika EXT counter cas kolem 2026-03-19 (EURUSD M30, EXAMPLE cfg)."""
from __future__ import annotations

import pandas as pd

from backtest.engine import BacktestEngine
from backtest.grid.translator import grid_dict_to_bot_config
from config.bot_config import BotConfig
from strategy.ext_logic import is_ext_wave
from strategy.wave_detection import detect_waves


def _example_cfg() -> BotConfig:
    row = {
        "timeframe": "M30",
        "wave_min_pct": 0.26,
        "min_opp_bars": 3,
        "rrr": 2.0,
        "fib_level": 0.5,
        "entry_mode": "market_fallback",
        "symbol": "EURUSD.x",
        "sl_fib_level": 0.8,
        "abort_fib_level": "shift_sl",
        "wave_plus": True,
        "ext_enabled": True,
        "ext_wave_min_pct": 0.76,
        "ext_counter_enabled": True,
        "ext_trade_both_sides_in_range": True,
        "trend_filter_enabled": True,
        "tp_mode": "bos_exit",
        "wave_position_enabled": True,
    }
    return grid_dict_to_bot_config(row)


def main() -> None:
    cfg = _example_cfg()
    df = pd.read_csv("data/EURUSD.x_M30.csv", parse_dates=["datetime"])
    df = df.rename(columns={"datetime": "time"})
    mask = (df["time"] >= "2026-03-18") & (df["time"] <= "2026-03-21")
    df = df.loc[mask].reset_index(drop=True)

    from strategy.wave_detection_pine import run_pine_wave_simulation

    waves, birth, ext_suppress, ext_forming = run_pine_wave_simulation(df, cfg)
    ext_waves = [w for w in waves if is_ext_wave(w, cfg)]
    print(f"EXT vlny v okne: {len(ext_waves)}")
    for w in ext_waves:
        print(
            f"  EXT {w['wave_time']} dir={w['dir']} move={w['move_pct']:.2f}% "
            f"draw_left={w['draw_left']} in_ext_range={w.get('in_ext_range')}"
        )

    print(f"ext_forming_first_bar: {ext_forming}")
    print(f"ext_counter_suppress_from_bar: {ext_suppress}")

    eng = BacktestEngine(cfg)
    closed = eng.run(df)
    wd = eng.wave_debug
    print("\nwave_debug EXT:")
    for k in sorted(wd):
        if "ext" in k.lower():
            print(f"  {k}: {wd[k]}")

    ext_ct_trades = [
        t
        for t in closed
        if str(getattr(t, "entry_tag", "")) == "ext_counter_time"
    ]
    print(f"\next_counter_time obchody: {len(ext_ct_trades)}")
    for t in ext_ct_trades:
        print(f"  {t.entry_time} dir={t.dir} wt={t.wave_time}")

    for ext in ext_waves:
        ext_wt = str(ext["wave_time"])
        ext_bi = birth.get(ext_wt)
        ext_bt = df.iloc[int(ext_bi)]["time"] if ext_bi is not None else None
        print(f"\nEXT birth: bar={ext_bi} time={ext_bt}")
        after = []
        for w in waves:
            wt = str(w["wave_time"])
            if wt == ext_wt:
                continue
            bi = birth.get(wt)
            if bi is None or ext_bi is None:
                continue
            if int(bi) > int(ext_bi):
                bt = pd.Timestamp(df.iloc[int(bi)]["time"]).to_pydatetime()
                after.append((wt, w["dir"], bt, w.get("in_ext_range")))
        print(f"Vlny po EXT (bar index > ext_bi): {len(after)}")
        for row in after[:12]:
            print(f"  {row[2]} wt={row[0]} dir={row[1]} in_range={row[3]}")
        # Prvni bar >= 21:00 po narozeni EXT
        if ext_bi is not None:
            for j in range(int(ext_bi), len(df)):
                t = pd.Timestamp(df.iloc[j]["time"]).to_pydatetime()
                if t.hour >= 21:
                    print(f"Prvni bar >=21:00 po EXT: {t} (bar {j})")
                    break


if __name__ == "__main__":
    main()
