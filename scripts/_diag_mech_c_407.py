"""Trace wave_sequence Mech C at bar 407."""
from __future__ import annotations

import pandas as pd
from dataclasses import replace

from backtest.grid.backtest_conf import generate_combinations, get_profile
from backtest.grid.translator import grid_dict_to_bot_config
from strategy.ext_range import reapply_ext_range_tags
from strategy.trend_bos import TrendState, maybe_update_trend_state_with_wave
from strategy.wave_detection_pine import run_pine_wave_simulation


def main() -> None:
    cfg = grid_dict_to_bot_config(generate_combinations(get_profile("testing"))[0])
    df = pd.read_csv("data/EURUSD_M30.csv", parse_dates=["datetime"]).rename(
        columns={"datetime": "time"}
    )
    df = df[(df["time"] >= "2025-05-10") & (df["time"] <= "2025-05-28")].reset_index(
        drop=True
    )
    waves, birth, _, _ = run_pine_wave_simulation(df, cfg)
    reapply_ext_range_tags(waves, cfg, df=df, wave_birth=birth)

    waves_by_extreme = {}
    for w in waves:
        dr = int(w["draw_right"])
        waves_by_extreme.setdefault(dr, []).append(w)

    state = TrendState()
    trend_established_by_ext = False
    ext_active_wave = None
    closes = df["close"].astype(float).to_numpy()
    n = len(df)

    for i in range(n):
        t = df.iloc[i]["time"]
        if t < pd.Timestamp("2025-05-21 16:00"):
            for w in waves_by_extreme.get(i, []):
                if w["wave_time"] == "202505211700":
                    pass
                maybe_update_trend_state_with_wave(state, w, cfg)
            continue
        if t > pd.Timestamp("2025-05-22 12:30"):
            break

        bar_close = float(closes[i])
        prev_lub = state.last_up_box_bottom
        prev_dir = state.direction
        prev_tebe = trend_established_by_ext

        # simplified Mech C
        mech_c = False
        if state.direction == "bull" and state.last_up_box_bottom is not None:
            if bar_close < state.last_up_box_bottom:
                mech_c = True
                if trend_established_by_ext:
                    trend_established_by_ext = False
                    state.last_up_box_bottom = None
                else:
                    state.direction = "bear"

        for w in waves_by_extreme.get(i, []):
            wt = w["wave_time"]
            maybe_update_trend_state_with_wave(state, w, cfg)
            if w.get("ext_post_range_terminator"):
                trend_established_by_ext = False
            flags = f"wave {wt} d={w['dir']} TERM={bool(w.get('ext_post_range_terminator'))}"
        else:
            flags = ""

        if (
            i >= 368
            and (
                mech_c
                or waves_by_extreme.get(i)
                or prev_lub != state.last_up_box_bottom
                or prev_dir != state.direction
            )
        ):
            print(
                f"{i} {t} c={bar_close:.5f} dir={state.direction} "
                f"lub={state.last_up_box_bottom} tebe={trend_established_by_ext} "
                f"mech_c={mech_c} {flags}"
            )


if __name__ == "__main__":
    main()
