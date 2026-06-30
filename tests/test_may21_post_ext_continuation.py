"""May 21 post-EXT3: BEAR1 korekce idx=1, WAVE4 idx=4, WAVE_BOS po HH."""
from __future__ import annotations

import pandas as pd

from backtest.engine import BacktestEngine
from backtest.grid.backtest_conf import generate_combinations, get_profile
from backtest.grid.translator import grid_dict_to_bot_config


def _testing_combo():
    combos = generate_combinations(get_profile("testing"))
    return grid_dict_to_bot_config(combos[0])


def test_may21_ext3_bear1_correction_then_wave4():
    cfg = _testing_combo()
    df = pd.read_csv("data/EURUSD_M30.csv", parse_dates=["datetime"]).rename(
        columns={"datetime": "time"}
    )
    df = df[(df["time"] >= "2025-05-10") & (df["time"] <= "2025-06-05")].reset_index(
        drop=True
    )
    eng = BacktestEngine(cfg)
    eng.run(df.copy(), retain_wave_snapshot=True)

    ext3 = "202505210930"
    bear1 = "202505211130"
    up4 = "202505211700"

    assert ext3 in eng.wave_sequence_info
    assert eng.wave_sequence_info[ext3].index_in_trend == 3

    info_bear = eng.wave_sequence_info[bear1]
    assert info_bear.index_in_trend == 1
    assert info_bear.is_bos_wave is False

    info_up = eng.wave_sequence_info[up4]
    assert info_up.index_in_trend == 4
    assert info_up.is_bos_wave is False

    w4 = eng.waves_by_wave_time[up4]
    assert not w4.get("in_ext_range", True)
    assert w4.get("ext_post_range_terminator") is True

    # Po WAVE4 HH: zadny seed — trend az pres WAVE_BOS (close pod WAVE4).
    post_w4_bear = "202505212300"
    assert post_w4_bear in eng.waves_by_wave_time
    assert eng.waves_by_wave_time[post_w4_bear].get("ext_post_trend_seed_dir") is None

    # WAVE_BOS: bear flip pod WAVE4 + BEAR idx 1,2,3
    bos_bear1 = "202505220730"
    assert bos_bear1 in eng.wave_sequence_info
    assert eng.wave_sequence_info[bos_bear1].index_in_trend == 1
    assert eng.wave_sequence_info[bos_bear1].is_bos_wave is True
    assert bos_bear1 in (eng._bos_wave_times or set())

    bear2 = "202505221300"
    bear3 = "202505222000"
    assert eng.wave_sequence_info[bear2].index_in_trend == 2
    assert eng.wave_sequence_info[bear3].index_in_trend == 3
