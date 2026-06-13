"""Diagnostika May 12-14 EXT číslování — read-only."""
from __future__ import annotations

import pandas as pd

from backtest.engine import BacktestEngine
from backtest.grid.backtest_conf import generate_combinations, get_profile
from backtest.grid.translator import grid_dict_to_bot_config
from strategy.ext_range import ext_scenario_classify


def main() -> None:
    cfg = grid_dict_to_bot_config(
        next(
            c
            for c in generate_combinations(get_profile("testing"))
            if c.get("trend_hh_hl_filter_enabled")
            and not c.get("wave_counter_two_sided_enabled")
        )
    )
    df = pd.read_csv("data/EURUSD.x_M30.csv", parse_dates=["datetime"]).rename(
        columns={"datetime": "time"}
    )
    df = df[(df["time"] >= "2025-05-10") & (df["time"] <= "2025-05-16")].reset_index(
        drop=True
    )
    eng = BacktestEngine(cfg)
    eng.run(df, retain_wave_snapshot=True)

    print("=== May 11-15 waves ===")
    for w in sorted(eng._all_waves, key=lambda x: int(x.get("draw_right", 0))):
        dr = int(w["draw_right"])
        if dr < 0 or dr >= len(df):
            continue
        t = df.iloc[dr]["time"]
        if t < pd.Timestamp("2025-05-11") or t > pd.Timestamp("2025-05-15 20:00"):
            continue
        wt = str(w["wave_time"])
        info = eng.wave_sequence_info.get(wt)
        ts = eng.trend_states_per_wave.get(wt)
        ext = "EXT" if w.get("is_ext") else "WAV"
        d = "UP" if w["dir"] == 1 else "DN"
        sc = "-"
        if w.get("is_ext") and ts:
            sc = ext_scenario_classify(
                w,
                ts,
                float(df.iloc[dr]["close"]),
                {
                    "last_up_box_bottom": ts.last_up_box_bottom,
                    "last_down_box_top": ts.last_down_box_top,
                },
            )
        td = ts.direction if ts else "?"
        idx = info.index_in_trend if info else None
        bos = info.is_bos_wave if info else False
        print(f"{wt} {t} {d} {ext} idx={idx} bos={bos} scen={sc} trend={td}")

    print("\n=== BOS events ===")
    for ev in eng.bos_flip_events or []:
        if pd.Timestamp(ev[0]) >= pd.Timestamp("2025-05-11"):
            print(ev)


if __name__ == "__main__":
    main()
