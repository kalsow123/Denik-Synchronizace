"""Test helperu is_trade_within_parent_ext_window."""
from strategy.ext_logic import (
    is_trade_within_parent_ext_window,
    ENTRY_TAG_EXT_SECONDARY,
    ENTRY_TAG_EXT_COUNTER_BOS,
    ENTRY_TAG_EXT_COUNTER_TIME,
)


class _FakeTrade:
    def __init__(self, *, entry_tag, wave_time, is_ext=True):
        self.entry_tag = entry_tag
        self.wave_time = wave_time
        self.is_ext = is_ext


def test_protected_when_parent_is_latest_wave():
    """Parent EXT je nejnovejsi narozena wave -> chranena."""
    trade = _FakeTrade(entry_tag=ENTRY_TAG_EXT_SECONDARY, wave_time="W3")
    births = {"W1": 10, "W2": 20, "W3": 30}
    assert is_trade_within_parent_ext_window(
        trade, wave_birth_by_time=births, bar_idx=35,
    ) is True


def test_not_protected_after_new_wave_born():
    """Po parent EXT vznikla nova wave -> ochrana konci."""
    trade = _FakeTrade(entry_tag=ENTRY_TAG_EXT_SECONDARY, wave_time="W2")
    births = {"W1": 10, "W2": 20, "W3": 30}
    # bar_idx 35, W3 vznikla na 30 = po parent W2
    assert is_trade_within_parent_ext_window(
        trade, wave_birth_by_time=births, bar_idx=35,
    ) is False


def test_protected_before_new_wave_is_born():
    """Parent W2 vznikla na 20, W3 vznikne az na 30. Na baru 25 je W2 nejnovejsi."""
    trade = _FakeTrade(entry_tag=ENTRY_TAG_EXT_SECONDARY, wave_time="W2")
    births = {"W1": 10, "W2": 20, "W3": 30}
    assert is_trade_within_parent_ext_window(
        trade, wave_birth_by_time=births, bar_idx=25,
    ) is True


def test_works_for_all_ext_block_types():
    births = {"W1": 10}
    
    e23 = _FakeTrade(entry_tag=ENTRY_TAG_EXT_SECONDARY, wave_time="W1")
    ecb = _FakeTrade(entry_tag=ENTRY_TAG_EXT_COUNTER_BOS, wave_time="W1")
    ect = _FakeTrade(entry_tag=ENTRY_TAG_EXT_COUNTER_TIME, wave_time="W1")
    
    for t in (e23, ecb, ect):
        assert is_trade_within_parent_ext_window(
            t, wave_birth_by_time=births, bar_idx=15,
        ) is True


def test_non_ext_trade_never_protected():
    trade = _FakeTrade(entry_tag="base", wave_time="W1", is_ext=False)
    births = {"W1": 10}
    assert is_trade_within_parent_ext_window(
        trade, wave_birth_by_time=births, bar_idx=15,
    ) is False


def test_unknown_parent_wave_not_protected():
    trade = _FakeTrade(entry_tag=ENTRY_TAG_EXT_SECONDARY, wave_time="UNKNOWN")
    births = {"W1": 10}
    assert is_trade_within_parent_ext_window(
        trade, wave_birth_by_time=births, bar_idx=15,
    ) is False


def test_empty_wave_time_not_protected():
    trade = _FakeTrade(entry_tag=ENTRY_TAG_EXT_SECONDARY, wave_time="")
    births = {"W1": 10}
    assert is_trade_within_parent_ext_window(
        trade, wave_birth_by_time=births, bar_idx=15,
    ) is False