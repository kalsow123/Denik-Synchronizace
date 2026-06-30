"""EXT counter TIME vs BOS — vzajemna blokace (peer mutex)."""
from __future__ import annotations

import pandas as pd

from backtest.engine import BacktestEngine
from backtest.grid.backtest_conf import generate_combinations, get_profile
from backtest.grid.translator import grid_dict_to_bot_config
from strategy.ext_logic import (
    ENTRY_TAG_EXT_COUNTER_BOS,
    ENTRY_TAG_EXT_COUNTER_TIME,
    has_open_ext_counter_peer,
)


class _FakeTrade:
    def __init__(self, *, entry_tag: str, is_ext: bool = True):
        self.entry_tag = entry_tag
        self.is_ext = is_ext


def test_has_open_ext_counter_peer_blocks_time_when_bos_open():
    bos = _FakeTrade(entry_tag=ENTRY_TAG_EXT_COUNTER_BOS)
    assert has_open_ext_counter_peer([bos], source="time") is True
    assert has_open_ext_counter_peer([bos], source="bos") is False


def test_has_open_ext_counter_peer_blocks_bos_when_time_open():
    time_tr = _FakeTrade(entry_tag=ENTRY_TAG_EXT_COUNTER_TIME)
    assert has_open_ext_counter_peer([time_tr], source="bos") is True
    assert has_open_ext_counter_peer([time_tr], source="time") is False


def test_may19_ext1_no_simultaneous_time_and_bos_counters():
    """
    Regrese: EXT 1 (May 19) — po EXT_BOS nesmi nasledovat EXT_COUNTER_TIME.
    """
    cfg = grid_dict_to_bot_config(
        next(
            c
            for c in generate_combinations(get_profile("testing"))
            if c.get("trend_hh_hl_filter_enabled")
            and not c.get("wave_counter_two_sided_enabled")
        )
    )
    df = pd.read_csv("data/EURUSD_M30.csv", parse_dates=["datetime"]).rename(
        columns={"datetime": "time"}
    )
    df = df[(df["time"] >= "2025-05-10") & (df["time"] <= "2025-05-22")].reset_index(
        drop=True
    )
    eng = BacktestEngine(cfg)
    eng.run(df, retain_wave_snapshot=True)

    open_counters = [
        t
        for t in eng.open_trades
        if str(getattr(t, "entry_tag", ""))
        in (ENTRY_TAG_EXT_COUNTER_TIME, ENTRY_TAG_EXT_COUNTER_BOS)
    ]
    assert len(open_counters) <= 1

    may19_evening = [
        t
        for t in open_counters
        if pd.Timestamp(t.entry_time) >= pd.Timestamp("2025-05-19 18:00")
        and pd.Timestamp(t.entry_time) <= pd.Timestamp("2025-05-20 00:00")
    ]
    assert len(may19_evening) <= 1
