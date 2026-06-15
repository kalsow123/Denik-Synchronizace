"""Simulate ext_climax_reversal guard: ext_active_wave must be None."""
from __future__ import annotations

import pandas as pd

from backtest.grid.backtest_conf import generate_combinations, get_profile
from backtest.grid.translator import grid_dict_to_bot_config
from strategy.ext_range import reapply_ext_range_tags, check_close_breaks_ext_extreme
from strategy.wave_detection_pine import run_pine_wave_simulation
from strategy.wave_sequence import compute_wave_sequence_info_per_wave, WaveSequenceInfo
from strategy.trend_bos import TrendState, maybe_update_trend_state_with_wave, _ghost_skip_wave


def compute_seq_with_guard(df, waves, cfg):
    """Copy of compute_wave_sequence with ext_climax guard while ext_active."""
    hh_hl_filter = bool(getattr(cfg, "trend_hh_hl_filter_enabled", False))
    waves_by_extreme = {}
    n = len(df)
    for w in waves:
        try:
            dr = int(w["draw_right"])
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= dr < n:
            waves_by_extreme.setdefault(dr, []).append(w)

    ext_active_wave = None
    ext_climax_reversal_dir = None
    climax_dir = climax_idx = climax_extreme = None
    trend_established_by_ext = False
    ext1_count_window = False
    ext1_counter_idx = 0
    last_ext1_counter_wt = None
    counter_up = counter_down = 0
    last_same_dir_up_wt = last_same_dir_down_wt = None
    result = {}
    state = TrendState()
    closes = df["close"].astype(float).to_numpy()

    for i in range(n):
        bar_close = float(closes[i])
        if ext_active_wave is not None:
            if check_close_breaks_ext_extreme(bar_close, ext_active_wave, 1 if int(ext_active_wave.get("dir",0))==1 else -1):
                ext_active_wave = None
            # skip mech B for brevity

        new_waves = waves_by_extreme.get(i, [])
        for w in new_waves:
            wt = str(w["wave_time"])
            wdir = int(w["dir"])
            is_ext = bool(w.get("is_ext"))

            if w.get("post_ext_trend_suppressed"):
                result[wt] = WaveSequenceInfo(None, None)
                continue

            # ... simplified: delegate climax reversal with GUARD
            if (
                ext_active_wave is None
                and ext_climax_reversal_dir is not None
                and wdir == ext_climax_reversal_dir
            ):
                wave_is_counter = (
                    (state.direction == "bull" and wdir == -1)
                    or (state.direction == "bear" and wdir == 1)
                )
                if wave_is_counter:
                    state.direction = "bear" if wdir == -1 else "bull"
                    if wdir == 1:
                        counter_up = 1
                        last_same_dir_up_wt = wt
                        counter_down = 0
                        last_same_dir_down_wt = None
                    else:
                        counter_down = 1
                        last_same_dir_down_wt = wt
                        counter_up = 0
                        last_same_dir_up_wt = None
                    result[wt] = WaveSequenceInfo(1, None, is_bos_wave=True)
                    ext_climax_reversal_dir = None
                    maybe_update_trend_state_with_wave(state, w, cfg)
                    continue

            if is_ext:
                from strategy.ext_range import ext_scenario_classify
                scenario = ext_scenario_classify(
                    w,
                    state,
                    bar_close,
                    {
                        "last_up_box_bottom": state.last_up_box_bottom,
                        "last_down_box_top": state.last_down_box_top,
                    },
                )
                if scenario == "C":
                    trend_established_by_ext = False
                    ext1_count_window = False
                    if wdir == 1:
                        counter_up += 1
                        result[wt] = WaveSequenceInfo(counter_up, last_same_dir_up_wt)
                        last_same_dir_up_wt = wt
                    else:
                        counter_down += 1
                        result[wt] = WaveSequenceInfo(counter_down, last_same_dir_down_wt)
                        last_same_dir_down_wt = wt
                    ext_active_wave = w
                    ext_climax_reversal_dir = -wdir
                    maybe_update_trend_state_with_wave(state, w, cfg)
                    continue

            if ext_active_wave is not None:
                wave_is_counter = (
                    (state.direction == "bull" and wdir == -1)
                    or (state.direction == "bear" and wdir == 1)
                )
                if wave_is_counter:
                    result[wt] = WaveSequenceInfo(None, None)
                    continue
                ext_climax_reversal_dir = None
                if _ghost_skip_wave(w, cfg, hh_hl_filter, result, wt):
                    continue
                if wdir == 1:
                    counter_up += 1
                    result[wt] = WaveSequenceInfo(counter_up, last_same_dir_up_wt)
                    last_same_dir_up_wt = wt
                else:
                    counter_down += 1
                    result[wt] = WaveSequenceInfo(counter_down, last_same_dir_down_wt)
                    last_same_dir_down_wt = wt
                maybe_update_trend_state_with_wave(state, w, cfg)
                continue

            # fallback trend-dir increment omitted for brevity
            result.setdefault(wt, WaveSequenceInfo(None, None))

    return result


def main():
    combo = next(
        c
        for c in generate_combinations(get_profile("testing"))
        if c.get("bos_entry_enable")
        and not c.get("pp_enabled")
        and c.get("wave_counter_two_sided_enabled")
    )
    cfg = grid_dict_to_bot_config(combo)
    df = pd.read_csv("data/EURUSD.x_M30.csv", parse_dates=["datetime"]).rename(
        columns={"datetime": "time"}
    )
    df = df[(df["time"] >= "2025-05-10") & (df["time"] <= "2025-06-05")].reset_index(
        drop=True
    )
    waves, birth, _, _ = run_pine_wave_simulation(df, cfg)
    reapply_ext_range_tags(waves, cfg, df=df, wave_birth=birth)
    seq_orig = compute_wave_sequence_info_per_wave(df, waves, cfg)
    seq_guard = compute_seq_with_guard(df, waves, cfg)

    focus = ["202505210930", "202505211130", "202505211700", "202505292100", "202505292300", "202505300400"]
    print("wt           orig_idx  guard_idx  orig_bos  guard_bos")
    for wt in focus:
        o = seq_orig.get(wt)
        g = seq_guard.get(wt)
        print(
            wt,
            getattr(o, "index_in_trend", None) if o else None,
            getattr(g, "index_in_trend", None) if g else None,
            getattr(o, "is_bos_wave", False) if o else False,
            getattr(g, "is_bos_wave", False) if g else False,
        )


if __name__ == "__main__":
    main()
