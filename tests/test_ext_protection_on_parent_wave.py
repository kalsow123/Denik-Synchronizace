"""Test: EXT pozice se nezavírá na své parent EXT vlně (mimo SL)."""
from strategy.ext_logic import (
    is_ext_block_trade_on_parent_wave,
    is_ext_secondary_trade,
    ENTRY_TAG_EXT_SECONDARY,
    ENTRY_TAG_EXT_COUNTER_BOS,
    ENTRY_TAG_EXT_COUNTER_TIME,
)


class _FakeTrade:
    def __init__(self, *, dir=1, entry_tag, is_ext=True, wave_time="W1"):
        self.dir = dir
        self.entry_tag = entry_tag
        self.is_ext = is_ext
        self.wave_time = wave_time


def test_ext_secondary_protected_on_parent_wave():
    trade = _FakeTrade(entry_tag=ENTRY_TAG_EXT_SECONDARY, wave_time="EXT_W1")
    assert is_ext_block_trade_on_parent_wave(trade, "EXT_W1") is True


def test_ext_secondary_not_protected_on_other_wave():
    trade = _FakeTrade(entry_tag=ENTRY_TAG_EXT_SECONDARY, wave_time="EXT_W1")
    assert is_ext_block_trade_on_parent_wave(trade, "EXT_W2") is False


def test_ext_counter_bos_protected_on_parent_wave():
    trade = _FakeTrade(entry_tag=ENTRY_TAG_EXT_COUNTER_BOS, wave_time="EXT_W1")
    assert is_ext_block_trade_on_parent_wave(trade, "EXT_W1") is True


def test_ext_counter_time_protected_on_parent_wave():
    trade = _FakeTrade(entry_tag=ENTRY_TAG_EXT_COUNTER_TIME, wave_time="EXT_W1")
    assert is_ext_block_trade_on_parent_wave(trade, "EXT_W1") is True


def test_non_ext_trade_not_protected():
    trade = _FakeTrade(entry_tag="base", is_ext=False, wave_time="EXT_W1")
    assert is_ext_block_trade_on_parent_wave(trade, "EXT_W1") is False


def test_is_ext_secondary_trade():
    e23 = _FakeTrade(entry_tag=ENTRY_TAG_EXT_SECONDARY)
    ecb = _FakeTrade(entry_tag=ENTRY_TAG_EXT_COUNTER_BOS)
    assert is_ext_secondary_trade(e23) is True
    assert is_ext_secondary_trade(ecb) is False
