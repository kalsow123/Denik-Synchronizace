"""Regrese: oprava 1 (EXT-1 reset při BOS). Oprava 2 vrácena — test 2 vypnut."""
from __future__ import annotations

import pandas as pd
import pytest

from backtest.engine import BacktestEngine
from backtest.grid.backtest_conf import generate_combinations, get_profile
from backtest.grid.translator import grid_dict_to_bot_config


def _cfg():
    combos = generate_combinations(get_profile("testing"))
    if not combos:
        pytest.skip("testing combo not found")
    return grid_dict_to_bot_config(combos[0])


@pytest.fixture
def full_may_df():
    df = pd.read_csv("data/EURUSD_M30.csv", parse_dates=["datetime"]).rename(
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


def test_may19_ext1_protection_blocks_ext_bos_close(full_may_df):
    """May 19: EXT 1 UP — longy nesmi zavrit EXT_BOS_CLOSE behem ochrany."""
    eng = BacktestEngine(_cfg())
    closed = eng.run(full_may_df, retain_wave_snapshot=True)
    per_bar = eng._ext1_protection_per_bar
    ext_bos_closes = [
        t
        for t in closed
        if getattr(t, "close_reason", "") == "EXT_BOS_CLOSE"
        and pd.Timestamp(t.close_time) >= pd.Timestamp("2025-05-19 13:30")
        and pd.Timestamp(t.close_time) <= pd.Timestamp("2025-05-19 23:30")
        and int(getattr(t, "dir", 0)) == 1
    ]
    assert ext_bos_closes == [], (
        f"EXT_BOS_CLOSE long behem EXT1 ochrany: {ext_bos_closes}"
    )
    wt = "202505190400"
    w = eng.waves_by_wave_time[wt]
    dr = int(w["draw_right"])
    start = dr + 1 if w.get("is_bos_wave") else dr
    assert per_bar[start] == 1, f"ochrana UP od baru {start} po EXT1"


def test_may30_ext_up_continues_numbering_after_up12(full_may_df):
    """May 30: po UP 1,2 musi EXT UP navazat jako idx 3, ne reset na 1."""
    eng = BacktestEngine(_cfg())
    eng.run(full_may_df, retain_wave_snapshot=True)
    info = eng.wave_sequence_info.get("202505292100")
    assert info is not None
    assert info.index_in_trend == 3
    assert info.is_bos_wave is False
    assert eng.wave_sequence_info["202505291300"].index_in_trend == 2


@pytest.mark.skip(reason="Oprava 2 vrácena — May 29 both-sides číslování zatím neplatí")
def test_oprava2_may29_up_after_ext_down_gets_number(full_may_df):
    """May 29 05:30: protisměrná UP v aktivní EXT oblasti nesmí zůstat bez čísla."""
    eng = BacktestEngine(_cfg())
    eng.run(full_may_df, retain_wave_snapshot=True)
    info = eng.wave_sequence_info.get("202505290530")
    assert info is not None
    assert info.index_in_trend is not None
    assert info.index_in_trend >= 1
