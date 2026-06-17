"""May 14: po EXT2 UP musi byt prvni protismerna (i is_ext) vlna idx=1."""
from __future__ import annotations

import pandas as pd

from backtest.engine import BacktestEngine
from backtest.grid.backtest_conf import generate_combinations, get_profile
from backtest.grid.translator import grid_dict_to_bot_config


def test_ext2_up_first_opposite_bear_is_idx_1_not_2():
    cfg = grid_dict_to_bot_config(generate_combinations(get_profile("testing"))[0])
    df = pd.read_csv("data/EURUSD.x_M30.csv", parse_dates=["datetime"]).rename(
        columns={"datetime": "time"}
    )
    df = df[(df["time"] >= "2025-05-12") & (df["time"] <= "2025-05-16")].reset_index(
        drop=True
    )
    eng = BacktestEngine(cfg)
    eng.run(df.copy(), retain_wave_snapshot=True)

    ext2 = "202505141230"
    bear_after = "202505142300"
    bear_before = "202505140900"

    assert eng.wave_sequence_info[ext2].index_in_trend == 2
    assert eng.wave_sequence_info[bear_before].index_in_trend == 1
    assert eng.wave_sequence_info[bear_after].index_in_trend == 1

    w = eng.waves_by_wave_time[bear_after]
    assert w.get("is_ext") is True
    assert w.get("in_ext_range") is True
