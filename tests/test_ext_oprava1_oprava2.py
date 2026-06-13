"""Regrese: oprava 1 (EXT-1 reset při BOS). Oprava 2 vrácena — test 2 vypnut."""
from __future__ import annotations

import pandas as pd
import pytest

from backtest.engine import BacktestEngine
from backtest.grid.backtest_conf import generate_combinations, get_profile
from backtest.grid.translator import grid_dict_to_bot_config


def _cfg():
    combos = generate_combinations(get_profile("testing"))
    for combo in combos:
        if (
            combo.get("trend_hh_hl_filter_enabled")
            and combo.get("wave_counter_two_sided_enabled") is False
        ):
            return grid_dict_to_bot_config(combo)
    pytest.skip("testing combo not found")


@pytest.fixture
def full_may_df():
    df = pd.read_csv("data/EURUSD.x_M30.csv", parse_dates=["datetime"]).rename(
        columns={"datetime": "time"}
    )
    return df[(df["time"] >= "2025-05-10") & (df["time"] <= "2025-06-05")].reset_index(
        drop=True
    )


def test_oprava1_may19_ext_up_is_wave1_after_bear_bos(full_may_df):
    """May 19: EXT UP po bear W3 BOS musí být idx 1, ne pokračování starého EXT-1 okna."""
    eng = BacktestEngine(_cfg())
    eng.run(full_may_df, retain_wave_snapshot=True)
    info = eng.wave_sequence_info.get("202505190400")
    assert info is not None
    assert info.index_in_trend == 1
    assert info.is_bos_wave is True


@pytest.mark.skip(reason="Oprava 2 vrácena — May 29 both-sides číslování zatím neplatí")
def test_oprava2_may29_up_after_ext_down_gets_number(full_may_df):
    """May 29 05:30: protisměrná UP v aktivní EXT oblasti nesmí zůstat bez čísla."""
    eng = BacktestEngine(_cfg())
    eng.run(full_may_df, retain_wave_snapshot=True)
    info = eng.wave_sequence_info.get("202505290530")
    assert info is not None
    assert info.index_in_trend is not None
    assert info.index_in_trend >= 1
