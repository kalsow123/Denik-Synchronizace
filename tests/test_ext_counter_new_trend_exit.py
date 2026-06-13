"""Test: EXT counter pozice se zavře v novém trendu na TP_WAVE_N."""
from strategy.wave_sequence import (
    should_close_trade_on_tp_wave_n,
    should_close_trade_on_bos_flip,
)
from strategy.ext_logic import (
    ENTRY_TAG_EXT_COUNTER_TIME,
    ENTRY_TAG_EXT_COUNTER_BOS,
    ENTRY_TAG_EXT_SECONDARY,
)


class _FakeTrade:
    def __init__(self, *, dir, entry_tag, is_ext, is_counter):
        self.dir = dir
        self.entry_tag = entry_tag
        self.is_ext = is_ext
        self.is_counter = is_counter


def test_ext_counter_bos_closes_on_tp_wave_n_same_direction():
    trade = _FakeTrade(
        dir=+1,
        entry_tag=ENTRY_TAG_EXT_COUNTER_BOS,
        is_ext=True,
        is_counter=True,
    )
    assert should_close_trade_on_tp_wave_n(trade, trend_dir=+1) is True


def test_ext_counter_time_closes_on_tp_wave_n_same_direction():
    trade = _FakeTrade(
        dir=-1,
        entry_tag=ENTRY_TAG_EXT_COUNTER_TIME,
        is_ext=True,
        is_counter=True,
    )
    assert should_close_trade_on_tp_wave_n(trade, trend_dir=-1) is True


def test_ext_counter_does_not_close_opposite_direction():
    """EXT counter v opačném směru než nová vlna se NEZAVÍRÁ."""
    trade = _FakeTrade(
        dir=+1,
        entry_tag=ENTRY_TAG_EXT_COUNTER_BOS,
        is_ext=True,
        is_counter=True,
    )
    assert should_close_trade_on_tp_wave_n(trade, trend_dir=-1) is False


def test_non_ext_counter_closes_on_tp_wave_n():
    """EXT_SECONDARY (E23_) se zavírá na TP_WAVE_N."""
    trade = _FakeTrade(
        dir=+1,
        entry_tag=ENTRY_TAG_EXT_SECONDARY,
        is_ext=True,
        is_counter=False,
    )
    assert should_close_trade_on_tp_wave_n(trade, trend_dir=-1) is True


def test_ext_counter_bos_flip_aligned_with_new_trend_not_closed():
    """BOS flip: EXT counter ve smeru noveho trendu zustava otevreny."""
    trade = _FakeTrade(
        dir=+1,
        entry_tag=ENTRY_TAG_EXT_COUNTER_BOS,
        is_ext=True,
        is_counter=True,
    )
    assert should_close_trade_on_bos_flip(
        trade, broken_dir=-1, flipped=True,
    ) is False


def test_ext_counter_bos_flip_wrong_dir_still_closed():
    trade = _FakeTrade(
        dir=-1,
        entry_tag=ENTRY_TAG_EXT_COUNTER_BOS,
        is_ext=True,
        is_counter=True,
    )
    assert should_close_trade_on_bos_flip(
        trade, broken_dir=-1, flipped=True,
    ) is True


def test_broken_dir_closes_normally():
    trade = _FakeTrade(
        dir=-1,
        entry_tag=ENTRY_TAG_EXT_COUNTER_TIME,
        is_ext=True,
        is_counter=True,
    )
    # Vsechny counter pozice maji nyni prezit BOS flip (i broken_dir bez flipu),
    # protoze se resi jen na SL nebo opacny BOS
    assert should_close_trade_on_bos_flip(
        trade, broken_dir=-1, flipped=False,
    ) is False
