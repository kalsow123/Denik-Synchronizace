"""Test helperů is_ext_secondary_trade a is_ext_block_trade_on_parent_wave."""
from types import SimpleNamespace

from strategy.ext_logic import (
    ENTRY_TAG_EXT_COUNTER_BOS,
    ENTRY_TAG_EXT_COUNTER_TIME,
    ENTRY_TAG_EXT_SECONDARY,
    is_ext_block_trade_on_parent_wave,
    is_ext_secondary_trade,
)


def _trade(*, entry_tag, is_ext=True, is_counter=False, wave_time="EXT_PARENT"):
    return SimpleNamespace(
        dir=1,
        entry_tag=entry_tag,
        is_ext=is_ext,
        is_counter=is_counter,
        wave_time=wave_time,
    )


def test_is_ext_secondary_trade():
    assert is_ext_secondary_trade(_trade(entry_tag=ENTRY_TAG_EXT_SECONDARY))
    assert not is_ext_secondary_trade(_trade(entry_tag=ENTRY_TAG_EXT_COUNTER_TIME))
    assert not is_ext_secondary_trade(_trade(entry_tag=ENTRY_TAG_EXT_COUNTER_BOS))
    assert not is_ext_secondary_trade(_trade(entry_tag="base", is_ext=False))
    assert not is_ext_secondary_trade(
        SimpleNamespace(dir=1, entry_tag=ENTRY_TAG_EXT_SECONDARY, is_ext=False)
    )


def test_is_ext_block_trade_on_parent_wave():
    parent = "202603192200"
    sec = _trade(entry_tag=ENTRY_TAG_EXT_SECONDARY, wave_time=parent)
    ect = _trade(entry_tag=ENTRY_TAG_EXT_COUNTER_TIME, wave_time=parent)
    ecb = _trade(entry_tag=ENTRY_TAG_EXT_COUNTER_BOS, wave_time=parent)

    assert is_ext_block_trade_on_parent_wave(sec, parent)
    assert is_ext_block_trade_on_parent_wave(ect, parent)
    assert is_ext_block_trade_on_parent_wave(ecb, parent)

    assert not is_ext_block_trade_on_parent_wave(sec, "OTHER_WT")
    assert not is_ext_block_trade_on_parent_wave(
        SimpleNamespace(dir=1, entry_tag="base", is_ext=False, wave_time=parent),
        parent,
    )
