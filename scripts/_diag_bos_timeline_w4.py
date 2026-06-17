"""Trace BOS timeline state around WAVE4 (May 21-22)."""
from __future__ import annotations

import pandas as pd

from backtest.grid.backtest_conf import generate_combinations, get_profile
from backtest.grid.translator import grid_dict_to_bot_config
from strategy.ext_range import reapply_ext_range_tags
from strategy.trend_bos import (
    TrendState,
    _advance_bos_timeline_bar,
    _build_waves_by_extreme_bar,
    compute_wave_birth_bars_pine,
    maybe_update_trend_state_with_wave,
)
from strategy.wave_detection_pine import run_pine_wave_simulation


def main() -> None:
    cfg = grid_dict_to_bot_config(generate_combinations(get_profile("testing"))[0])
    df = pd.read_csv("data/EURUSD.x_M30.csv", parse_dates=["datetime"]).rename(
        columns={"datetime": "time"}
    )
    df = df[(df["time"] >= "2025-05-10") & (df["time"] <= "2025-05-28")].reset_index(
        drop=True
    )
    waves, birth, _, _ = run_pine_wave_simulation(df, cfg)
    reapply_ext_range_tags(waves, cfg, df=df, wave_birth=birth)

    n = len(df)
    waves_by_birth = {}
    for w in waves:
        b = birth.get(w["wave_time"])
        if b is not None:
            waves_by_birth.setdefault(int(b), []).append(w)
    waves_by_ext = _build_waves_by_extreme_bar(waves, n)

    state = TrendState()
    closes = df["close"].astype(float).to_numpy()
    t_start = pd.Timestamp("2025-05-21 16:00")
    t_end = pd.Timestamp("2025-05-23 08:00")

    print("bar time close dir tebe lub ldt flip")
    for i in range(n):
        t = df.iloc[i]["time"]
        if t < t_start:
            prev = state
            state, flipped = _advance_bos_timeline_bar(
                state,
                float(closes[i]),
                i,
                cfg=cfg,
                waves_by_extreme_bar=waves_by_ext,
                waves_by_birth_bar=waves_by_birth,
            )
            continue
        if t > t_end:
            break

        prev_dir = state.direction
        prev_lub = state.last_up_box_bottom
        prev_tebe = state.trend_established_by_ext
        state, flipped = _advance_bos_timeline_bar(
            state,
            float(closes[i]),
            i,
            cfg=cfg,
            waves_by_extreme_bar=waves_by_ext,
            waves_by_birth_bar=waves_by_birth,
        )
        ext_waves = waves_by_ext.get(i, [])
        birth_waves = waves_by_birth.get(i, [])
        flags = []
        for w in ext_waves:
            flags.append(f"ext:{w['wave_time']} d={w['dir']}")
        for w in birth_waves:
            supp = "SUPP" if w.get("post_ext_trend_suppressed") else ""
            seed = w.get("ext_post_trend_seed_dir")
            flags.append(
                f"birth:{w['wave_time']} d={w['dir']} {supp} seed={seed}"
            )
        flip_s = {-1: "BEAR", 1: "BULL"}.get(flipped, "")
        if (
            flipped
            or ext_waves
            or birth_waves
            or prev_lub != state.last_up_box_bottom
            or prev_dir != state.direction
            or float(closes[i]) < (prev_lub or 999)
        ):
            print(
                f"{i:4} {t} c={closes[i]:.5f} dir={state.direction} "
                f"tebe={state.trend_established_by_ext}(was {prev_tebe}) "
                f"lub={state.last_up_box_bottom} flip={flip_s} "
                f"{' '.join(flags)}"
            )


if __name__ == "__main__":
    main()
