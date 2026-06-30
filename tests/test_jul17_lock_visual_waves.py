"""Jul 17: post-EXT lock bear segmenty sloučené do jednoho visual boxu."""
from __future__ import annotations

import pandas as pd
import pytest

from backtest.engine import BacktestEngine
from backtest.grid.backtest_conf import generate_combinations, get_profile
from backtest.grid.translator import grid_dict_to_bot_config


def _testing_combo():
    for combo in generate_combinations(get_profile("testing")):
        if combo.get("bos_entry_enable"):
            return grid_dict_to_bot_config(combo)
    raise RuntimeError("testing combo not found")


def test_jul17_lock_bear_merged_to_low_in_html():
    cfg = _testing_combo()
    df = pd.read_csv("data/EURUSD_M30.csv", parse_dates=["datetime"]).rename(
        columns={"datetime": "time"}
    )
    df = df[
        (df["time"] >= "2025-05-10") & (df["time"] <= "2025-07-25")
    ].reset_index(drop=True)
    eng = BacktestEngine(cfg)
    eng.run(df.copy(), retain_wave_snapshot=True)

    vis_wt = {str(w.get("wave_time")) for w in eng.last_waves_for_visual}
    vis_by_wt = {str(w["wave_time"]): w for w in eng.last_waves_for_visual}

    bear_head = "202507170430"
    bear_mid = "202507170930"
    bear_low = "202507171530"
    bounce = "202507171130"

    assert bear_head in vis_wt
    assert bear_mid not in vis_wt
    assert bear_low not in vis_wt
    assert bounce not in vis_wt

    merged = vis_by_wt[bear_head]
    assert merged.get("_visual_lock_merged") is True
    assert float(merged["box_bottom"]) == pytest.approx(1.15564, abs=1e-5)

    for wt in (bear_mid, bear_low):
        info = eng.wave_sequence_info.get(wt)
        assert info is not None
        assert info.index_in_trend is None
