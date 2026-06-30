"""Trace swing + BOS around bar 414 (May 22 2025)."""
from __future__ import annotations

import pandas as pd

from backtest.grid.backtest_conf import generate_combinations, get_profile
from backtest.grid.translator import grid_dict_to_bot_config
from strategy.trend_bos import (
    TrendState,
    _bos_close_flip_with_forgive,
    _maybe_seed_state_from_ext_post_trend,
    maybe_update_trend_state_with_wave,
)
from strategy.wave_detection import detect_waves
from strategy.wave_detection_pine import compute_wave_birth_bars_pine

TARGET = {"202505221300", "202505222230", "202505230430", "202505231430"}


def main() -> None:
    combos = generate_combinations(get_profile("testing"))
    combo = next(
        c
        for c in combos
        if c.get("trend_hh_hl_filter_enabled")
        and c.get("wave_counter_two_sided_enabled")
        and c.get("tp_mode") == "wave_target_n"
    )
    cfg = grid_dict_to_bot_config(combo)
    df = pd.read_csv("data/EURUSD_M30.csv", parse_dates=["datetime"]).rename(
        columns={"datetime": "time"}
    )
    df = df[(df["time"] >= "2025-05-10") & (df["time"] <= "2025-06-05")].reset_index(
        drop=True
    )
    waves = detect_waves(df, cfg)
    bb = compute_wave_birth_bars_pine(df, cfg)

    by_dr: dict[int, list] = {}
    by_birth: dict[int, list] = {}
    for w in waves:
        dr = int(w["draw_right"])
        by_dr.setdefault(dr, []).append(w)
        b = bb.get(str(w["wave_time"]))
        if b is not None:
            by_birth.setdefault(int(b), []).append(w)

    state = TrendState()
    closes = df["close"].astype(float).to_numpy()
    for i in range(380, 470):
        bc = float(closes[i])
        ft, ns = _bos_close_flip_with_forgive(state, bc)
        if ft != 0:
            print(
                f"bar {i} {df.time.iloc[i]} close={bc:.5f} "
                f"flip={ft} up_bot={state.last_up_box_bottom} down_top={state.last_down_box_top}"
            )
        if ft == -1:
            state = ns
            state.direction = "bear"
            state.is_bos_wave_pending = True
        elif ft == 1:
            state = ns
            state.direction = "bull"
            state.is_bos_wave_pending = True
        else:
            state = ns

        for w in by_birth.get(i, []):
            wt = str(w["wave_time"])
            if wt in TARGET:
                print(f"  birth {wt} dir={w['dir']} before up_bot={state.last_up_box_bottom}")
            state = _maybe_seed_state_from_ext_post_trend(state, w)
            maybe_update_trend_state_with_wave(state, w, cfg)
            if wt in TARGET:
                print(
                    f"  birth {wt} after dir={state.direction} "
                    f"up_bot={state.last_up_box_bottom} down_top={state.last_down_box_top}"
                )

        for w in by_dr.get(i, []):
            wt = str(w["wave_time"])
            if wt in TARGET:
                print(
                    f"  draw_right {wt} dir={w['dir']} trend={state.direction} "
                    f"pending={state.is_bos_wave_pending}"
                )


if __name__ == "__main__":
    main()
