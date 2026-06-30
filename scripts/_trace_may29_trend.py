"""Sledování směru trendu bar-po-baru kolem May 29."""
from __future__ import annotations

import pandas as pd

from backtest.grid.backtest_conf import generate_combinations, get_profile
from backtest.grid.translator import grid_dict_to_bot_config
from strategy.trend_bos import compute_trend_states_per_bar


def main():
    combos = generate_combinations(get_profile("testing"))
    combo = next(
        c
        for c in combos
        if c.get("trend_hh_hl_filter_enabled")
        and c.get("wave_counter_two_sided_enabled") is False
    )
    cfg = grid_dict_to_bot_config(combo)
    df = pd.read_csv("data/EURUSD_M30.csv", parse_dates=["datetime"]).rename(
        columns={"datetime": "time"}
    )
    df = df[(df["time"] >= "2025-05-28") & (df["time"] <= "2025-05-30")].reset_index(
        drop=True
    )
    from backtest.engine import BacktestEngine

    eng = BacktestEngine(cfg)
    eng.run(df, retain_wave_snapshot=False)
    # rebuild waves for per-bar
    from backtest.wave_detection_pine import detect_all_waves_pine

    waves, birth, *_ = detect_all_waves_pine(df, cfg)
    per_bar = compute_trend_states_per_bar(df, waves, cfg)
    for i in range(len(df)):
        t = df.iloc[i]["time"]
        if t < pd.Timestamp("2025-05-29 00:00") or t > pd.Timestamp("2025-05-30 00:00"):
            continue
        st = per_bar[i] if i < len(per_bar) else None
        d = st.direction if st else "?"
        print(f"{t} bar={i} trend={d} close={df.iloc[i]['close']:.5f}")
