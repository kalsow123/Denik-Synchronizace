"""Sken vsech vikendovych gapu v backtest range. Pro kazdy gap vypise vlny pres/u gapu."""
from __future__ import annotations

import pandas as pd

from config.bot_config import BotConfig
from strategy.wave_detection_pine import (
    _compute_after_data_gap_mask,
    run_pine_wave_simulation,
)


def main() -> None:
    cfg = BotConfig(
        wave_min_pct=0.26,
        min_opp_bars=3,
        entry_fib_level=0.5,
        sl_fib_level=0.8,
        rrr=2.0,
        wave_plus=True,
        ext_enabled=True,
        ext_wave_min_pct=0.76,
    )
    df = pd.read_csv("data/EURUSD_M30.csv", parse_dates=["datetime"]).rename(
        columns={"datetime": "time"}
    )
    full = df[(df["time"] >= "2026-03-03") & (df["time"] <= "2026-05-10")].reset_index(drop=True)
    waves, _, _, _ = run_pine_wave_simulation(full, cfg)
    gm = _compute_after_data_gap_mask(full["time"])
    gaps = [i for i, v in enumerate(gm) if v]

    def fmt(w):
        L = int(w["draw_left"])
        R = int(w["draw_right"])
        return (
            f"t={w['wave_time']} dir={int(w['dir']):+d} "
            f"L={L}({full['time'].iloc[L]}) R={R}({full['time'].iloc[R]}) "
            f"move={float(w['move_pct']):.3f} ext={bool(w.get('is_ext', False))} "
            f"top={float(w['box_top']):.5f} bot={float(w['box_bottom']):.5f}"
        )

    for gi in gaps:
        prev = full.iloc[gi - 1]
        cur = full.iloc[gi]
        diff = float(cur["open"]) - float(prev["close"])
        print(f"\n=== Gap @ idx {gi}  prev {prev['time']} (C{prev['close']:.5f}) -> "
              f"cur {cur['time']} (O{cur['open']:.5f})  jump={diff:+.5f} ===")
        spans = [w for w in waves if int(w["draw_left"]) <= gi <= int(w["draw_right"])]
        if spans:
            for w in spans:
                print("  SPANS: " + fmt(w))
        else:
            prev_w = [w for w in waves if int(w["draw_right"]) < gi][-3:]
            post_w = [w for w in waves if int(w["draw_left"]) > gi][:3]
            for w in prev_w:
                print("  PREV : " + fmt(w))
            for w in post_w:
                print("  POST : " + fmt(w))


if __name__ == "__main__":
    main()
