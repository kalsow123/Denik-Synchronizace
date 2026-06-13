"""Primarni WAVE z EXT vlny — chovani jako WAVE_COUNTER pri BOS / TP_WAVE_N."""
from strategy.ext_logic import ENTRY_TAG_BASE
from strategy.wave_sequence import (
    should_close_trade_on_bos_flip,
    should_close_trade_on_tp_wave_n,
)


class _FakeTrade:
    def __init__(self, *, dir, entry_tag=ENTRY_TAG_BASE, is_ext=True, is_counter=False):
        self.dir = dir
        self.entry_tag = entry_tag
        self.is_ext = is_ext
        self.is_counter = is_counter
        self.is_two_sided_mirror = False


def test_ext_primary_not_closed_per_bar_against_trend():
    """BUY v bear trendu (broken_dir=+1) — per-bar BOS nezavira."""
    trade = _FakeTrade(dir=+1)
    assert should_close_trade_on_bos_flip(
        trade, broken_dir=+1, flipped=False,
    ) is False


def test_ext_primary_survives_flip_aligned_with_new_trend():
    """Po flipu bull: BUY (novy trend) prezije."""
    trade = _FakeTrade(dir=+1)
    assert should_close_trade_on_bos_flip(
        trade, broken_dir=-1, flipped=True,
    ) is False


def test_ext_primary_closed_on_flip_against_new_trend():
    """Po flipu bull: SELL (proti novemu trendu) se zavre."""
    trade = _FakeTrade(dir=-1)
    assert should_close_trade_on_bos_flip(
        trade, broken_dir=-1, flipped=True,
    ) is True


def test_ext_primary_closes_on_tp_wave_n_when_aligned():
    trade = _FakeTrade(dir=+1)
    assert should_close_trade_on_tp_wave_n(trade, trend_dir=+1) is True


def test_ext_primary_not_closed_on_tp_wave_n_opposite():
    trade = _FakeTrade(dir=+1)
    assert should_close_trade_on_tp_wave_n(trade, trend_dir=-1) is False
