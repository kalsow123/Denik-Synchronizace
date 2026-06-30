"""May 30 post-EXT: close-BOS po WAVE4, bear chain, konec post_ext lock."""
from __future__ import annotations

import pandas as pd

from backtest.engine import BacktestEngine
from backtest.grid.backtest_conf import generate_combinations, get_profile
from backtest.grid.translator import grid_dict_to_bot_config
from backtest.visual_wave_filter import wave_passes_visual_filter


def _testing_combo():
    combos = generate_combinations(get_profile("testing"))
    return grid_dict_to_bot_config(combos[0])


def test_may30_wave4_break_registers_bear_bos_and_bear_chain():
    cfg = _testing_combo()
    df = pd.read_csv("data/EURUSD_M30.csv", parse_dates=["datetime"]).rename(
        columns={"datetime": "time"}
    )
    df = df[(df["time"] >= "2025-05-10") & (df["time"] <= "2025-06-05")].reset_index(
        drop=True
    )
    eng = BacktestEngine(cfg)
    eng.run(df.copy(), retain_wave_snapshot=True)

    flips = eng._close_bos_flip_bar_indices or set()
    may_flips = [
        df.iloc[i]["time"]
        for i in sorted(flips)
        if pd.Timestamp("2025-05-30") <= df.iloc[i]["time"] <= pd.Timestamp("2025-06-03")
    ]
    assert may_flips, f"expected bear BOS flip May30-Jun3, got none in {flips}"

    bear1 = "202505301200"
    bear2 = "202505301630"
    info1 = eng.wave_sequence_info[bear1]
    info2 = eng.wave_sequence_info[bear2]

    assert info1.index_in_trend == 1
    assert info1.is_bos_wave is True
    assert bear1 in (eng._bos_wave_times or set())

    assert info2.index_in_trend == 2
    assert info2.prev_same_dir_in_trend_wave_time == bear1

    w1 = eng.waves_by_wave_time[bear1]
    assert not w1.get("post_ext_trend_suppressed")

    bos_times = set(eng._visual_bos_wave_times or eng._bos_wave_times or set())
    assert wave_passes_visual_filter(w1, cfg, bos_wave_times=bos_times)
    assert wave_passes_visual_filter(
        eng.waves_by_wave_time[bear2], cfg, bos_wave_times=bos_times
    )
