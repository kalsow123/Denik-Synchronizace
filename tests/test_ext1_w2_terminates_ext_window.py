"""EXT1 + WAVE2 same dir ukonci EXT okno; protisměr pred BOS nema idx 1,2."""
from __future__ import annotations

import pandas as pd

from backtest.engine import BacktestEngine
from backtest.grid.backtest_conf import generate_combinations, get_profile
from backtest.grid.translator import grid_dict_to_bot_config


def test_ext1_down_wave2_down_ends_ext_no_bull_idx_before_bos():
    cfg = grid_dict_to_bot_config(generate_combinations(get_profile("testing"))[0])
    df = pd.read_csv("data/EURUSD_M30.csv", parse_dates=["datetime"]).rename(
        columns={"datetime": "time"}
    )
    df = df[(df["time"] >= "2025-04-21") & (df["time"] <= "2025-04-23")].reset_index(
        drop=True
    )
    eng = BacktestEngine(cfg)
    eng.run(df.copy(), retain_wave_snapshot=True)

    ext1 = "202504211800"
    wave2 = "202504220330"
    bounce_up = "202504220830"

    assert eng.wave_sequence_info[ext1].index_in_trend == 1
    assert eng.wave_sequence_info[wave2].index_in_trend == 2

    # Mezilehlý bounce v EXT okně nemá dostat protisměrné idx 1/2
    assert eng.wave_sequence_info["202504212200"].index_in_trend is None

    # První UP s číslem až po BOS nad WAVE2 (mimo aktivní protisměr v EXT okně)
    assert eng.wave_sequence_info[bounce_up].index_in_trend == 1
    assert eng.wave_sequence_info[bounce_up].is_bos_wave is True
