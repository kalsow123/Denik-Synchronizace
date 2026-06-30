"""May 22–23: BOS na W3 swing, první UP po bear má číslo."""
from __future__ import annotations

import pandas as pd
import pytest

from backtest.engine import BacktestEngine
from backtest.grid.backtest_conf import generate_combinations, get_profile
from backtest.grid.translator import grid_dict_to_bot_config
from backtest.visual_wave_filter import wave_passes_visual_filter


def _testing_cfg():
    combos = generate_combinations(get_profile("testing"))
    for combo in combos:
        if (
            combo.get("wf_enabled")
            and combo.get("trend_hh_hl_filter_enabled")
            and combo.get("tp_mode") == "wave_target_n"
            and combo.get("wave_counter_two_sided_enabled") is False
            and combo.get("pp_enabled") is False
        ):
            return grid_dict_to_bot_config(combo)
    pytest.skip("testing combo not found")


@pytest.fixture
def may_df():
    path = "data/EURUSD_M30.csv"
    df = pd.read_csv(path, parse_dates=["datetime"]).rename(columns={"datetime": "time"})
    return df[(df["time"] >= "2025-05-10") & (df["time"] <= "2025-05-26")].reset_index(
        drop=True
    )


def test_may23_bull_bos_swing_on_wave3_high(may_df):
    cfg = _testing_cfg()
    eng = BacktestEngine(cfg)
    eng.run(may_df, retain_wave_snapshot=True)

    bull_events = [
        ev
        for ev in (eng.bos_flip_events or [])
        if "bull" in str(ev[2])
        and pd.Timestamp(ev[0]) >= pd.Timestamp("2025-05-23")
    ]
    assert bull_events, "očekáván bull BOS po 23.5."
    _t, swing, _label, _t0 = bull_events[0]
    assert abs(float(swing) - 1.13146) < 1e-5


def test_may23_first_up_after_bear_gets_wave1(may_df):
    cfg = _testing_cfg()
    eng = BacktestEngine(cfg)
    eng.run(may_df, retain_wave_snapshot=True)
    bos = set(getattr(eng, "_visual_bos_wave_times", set()) or set())

    wt_early = "202505230430"
    wt_late = "202505231430"
    info_early = eng.wave_sequence_info.get(wt_early)
    info_late = eng.wave_sequence_info.get(wt_late)

    assert info_early is not None
    assert info_early.index_in_trend == 1
    assert info_early.is_bos_wave is True

    assert info_late is not None
    assert info_late.index_in_trend == 2

    for w in eng._all_waves:
        wt = str(w["wave_time"])
        if wt not in (wt_early, wt_late):
            continue
        if not wave_passes_visual_filter(w, cfg, bos_wave_times=bos):
            continue
        assert w.get("index_in_trend") is not None
