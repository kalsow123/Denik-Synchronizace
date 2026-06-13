"""Live parita: EXT block ochrana na parent vlně (TP_WAVE_N, BOS flip, EXT BOS close)."""
from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

if "MetaTrader5" not in sys.modules:
    _mt5_stub = MagicMock()
    _mt5_stub.POSITION_TYPE_BUY = 0
    _mt5_stub.POSITION_TYPE_SELL = 1
    _mt5_stub.TRADE_RETCODE_DONE = 10009
    sys.modules["MetaTrader5"] = _mt5_stub

import MetaTrader5 as mt5

from config.bot_config import LIVE_BOT_CONFIG
from infra.orders import (
    _Mt5PositionTradeView,
    close_flip_follower_positions_on_bos,
    close_positions_by_direction,
    close_positions_on_tp_wave_n,
)
from strategy.ext_logic import is_ext_block_trade_on_parent_wave


def test_mt5_trade_view_wave_time_from_ext_comment():
    tv = _Mt5PositionTradeView(pos_dir=-1, comment="E23_PARENT_WT")
    assert tv.wave_time == "PARENT_WT"
    assert is_ext_block_trade_on_parent_wave(tv, "PARENT_WT")


def test_tp_wave_n_skips_ext_block_on_parent_wave():
    parent = "EXT_PARENT"
    pos = type(
        "P",
        (),
        {
            "ticket": 1,
            "magic": int(LIVE_BOT_CONFIG.magic),
            "type": 1,
            "volume": 0.01,
            "price_open": 1.12,
            "sl": 1.14,
            "tp": 0.0,
            "comment": f"E23_{parent}",
        },
    )()
    info = type("I", (), {"point": 0.00001, "digits": 5})()

    with patch("infra.orders.mt5.positions_get", return_value=[pos]), patch(
        "infra.orders.mt5.symbol_info", return_value=info,
    ), patch("infra.orders._close_mt5_position_market", return_value=True) as close_mkt:
        stats = close_positions_on_tp_wave_n(
            LIVE_BOT_CONFIG,
            trend_dir=-1,
            bar_high=1.124,
            bar_low=1.110,
            bar_close=1.118,
            current_wave_time=parent,
        )

    assert stats["trend_dir_closed"] == 0
    assert stats["ext_parent_protected"] == 1
    assert not close_mkt.called


def test_tp_wave_n_sl_closes_ext_block_on_parent_wave():
    parent = "EXT_PARENT"
    pos = type(
        "P",
        (),
        {
            "ticket": 1,
            "magic": int(LIVE_BOT_CONFIG.magic),
            "type": 1,
            "volume": 0.01,
            "price_open": 1.12,
            "sl": 1.13,
            "tp": 0.0,
            "comment": f"E23_{parent}",
        },
    )()
    info = type("I", (), {"point": 0.00001, "digits": 5})()

    with patch("infra.orders.mt5.positions_get", return_value=[pos]), patch(
        "infra.orders.mt5.symbol_info", return_value=info,
    ), patch("infra.orders._close_mt5_position_market", return_value=True) as close_mkt:
        stats = close_positions_on_tp_wave_n(
            LIVE_BOT_CONFIG,
            trend_dir=-1,
            bar_high=1.135,
            bar_low=1.110,
            bar_close=1.118,
            current_wave_time=parent,
        )

    assert stats["sl_protected"] == 1
    assert close_mkt.called


def test_flip_follower_skips_ext_block_on_bos_parent_wave():
    parent = "EXT_PARENT"
    pos = type(
        "P",
        (),
        {
            "ticket": 2,
            "magic": int(LIVE_BOT_CONFIG.magic),
            "type": 1,
            "volume": 0.01,
            "price_open": 1.12,
            "sl": 1.14,
            "tp": 0.0,
            "comment": f"ECT_{parent}",
        },
    )()
    info = type("I", (), {"point": 0.00001, "digits": 5})()

    with patch("infra.orders.mt5.positions_get", return_value=[pos]), patch(
        "infra.orders.mt5.symbol_info", return_value=info,
    ), patch("infra.orders._close_mt5_position_market", return_value=True) as close_mkt:
        n = close_flip_follower_positions_on_bos(
            LIVE_BOT_CONFIG,
            broken_dir=+1,
            bar_high=1.124,
            bar_low=1.110,
            protect_ext_block_from_wave=parent,
        )

    assert n == 0
    assert not close_mkt.called


def test_ext_bos_close_skips_secondary_on_parent_without_sl():
    parent = "EXT_PARENT"
    pos = type(
        "P",
        (),
        {
            "ticket": 3,
            "magic": int(LIVE_BOT_CONFIG.magic),
            "type": 0,
            "volume": 0.01,
            "price_open": 1.12,
            "sl": 1.10,
            "tp": 0.0,
            "comment": f"E23_{parent}",
        },
    )()
    info = type("I", (), {"point": 0.00001, "digits": 5})()

    with patch("infra.orders.mt5.positions_get", return_value=[pos]), patch(
        "infra.orders.mt5.symbol_info", return_value=info,
    ), patch("infra.orders._close_mt5_position_market", return_value=True) as close_mkt:
        n = close_positions_by_direction(
            LIVE_BOT_CONFIG,
            direction=+1,
            reason="EXT_BOS_CLOSE",
            protect_ext_block_from_wave=parent,
            bar_high=1.124,
            bar_low=1.110,
        )

    assert n == 0
    assert not close_mkt.called
