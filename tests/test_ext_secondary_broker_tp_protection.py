"""E23_ (EXT 0,236): bez broker TP + ochrana proti TP close v parent EXT okne."""
from __future__ import annotations

from datetime import datetime

from backtest.engine import BacktestEngine, OpenTrade, PendingOrder
from config.bot_config import LIVE_BOT_CONFIG
from strategy.ext_logic import (
    ENTRY_TAG_EXT_SECONDARY,
    compute_ext_secondary_take_profit,
)


def _e23_trade(*, wave_time: str = "EXT1", direction: int = -1, tp: float | None) -> OpenTrade:
    po = PendingOrder(
        signal={"wave_time": wave_time, "dir": direction},
        order_type="SELL_LIMIT",
        entry_price=1.1200,
        sl=1.1250,
        tp=tp,
        lot=0.1,
        created_bar=5,
        created_time=datetime(2026, 5, 1, 10, 0),
        dir_override=direction,
        entry_tag=ENTRY_TAG_EXT_SECONDARY,
        is_ext=True,
    )
    return OpenTrade(
        po, 6, 1.1200, datetime(2026, 5, 1, 10, 30), "LIMIT", 1.1250, tp,
    )


def _wave_trade_with_tp(*, direction: int = 1) -> OpenTrade:
    po = PendingOrder(
        signal={"wave_time": "W1", "dir": direction},
        order_type="BUY_LIMIT",
        entry_price=1.1300,
        sl=1.1250,
        tp=1.1400,
        lot=0.1,
        created_bar=5,
        created_time=datetime(2026, 5, 1, 10, 0),
        dir_override=direction,
    )
    return OpenTrade(
        po, 6, 1.1300, datetime(2026, 5, 1, 10, 30), "LIMIT", 1.1250, 1.1400,
    )


def test_compute_ext_secondary_take_profit_always_none():
    assert compute_ext_secondary_take_profit(
        LIVE_BOT_CONFIG, 1.12, 1.125, is_buy=False,
    ) is None


def test_check_sl_tp_blocks_e23_broker_tp_in_parent_window():
    parent = "EXT1"
    eng = BacktestEngine.__new__(BacktestEngine)
    eng.wave_birth_by_time = {parent: 5}
    eng.wave_debug = {}
    eng._ext1_protection_per_bar = [0] * 20
    eng.cfg = type("C", (), {"ext1_protect_positions_until_wave2": False})()
    eng.open_trades = [_e23_trade(wave_time=parent, tp=1.1150)]
    eng.closed_trades = []
    eng._append_closed_trade = lambda ct, _t: eng.closed_trades.append(ct)
    eng._make_closed = lambda trade, bar_idx, close_price, bar_time, reason: type(
        "CT", (), {"trade": trade, "reason": reason}
    )()

    eng._check_sl_tp(
        8,
        datetime(2026, 5, 1, 12, 0),
        high=1.1180,
        low=1.1140,
    )

    assert len(eng.open_trades) == 1
    assert eng.closed_trades == []
    assert eng.wave_debug.get("ext_secondary_protected_broker_tp", 0) == 1


def test_check_sl_tp_still_closes_e23_broker_tp_after_next_wave():
    parent = "EXT1"
    eng = BacktestEngine.__new__(BacktestEngine)
    eng.wave_birth_by_time = {parent: 5, "W2": 7}
    eng.wave_debug = {}
    eng._ext1_protection_per_bar = [0] * 20
    eng.cfg = type("C", (), {"ext1_protect_positions_until_wave2": False})()
    eng.open_trades = [_e23_trade(wave_time=parent, tp=1.1150)]
    eng.closed_trades = []
    eng._append_closed_trade = lambda ct, _t: eng.closed_trades.append(ct)
    eng._make_closed = lambda trade, bar_idx, close_price, bar_time, reason: type(
        "CT", (), {"trade": trade, "reason": reason}
    )()

    eng._check_sl_tp(
        8,
        datetime(2026, 5, 1, 12, 0),
        high=1.1180,
        low=1.1140,
    )

    assert eng.open_trades == []
    assert len(eng.closed_trades) == 1
    assert eng.closed_trades[0].reason == "TP"


def test_check_sl_tp_wave_trade_broker_tp_unchanged():
    eng = BacktestEngine.__new__(BacktestEngine)
    eng.wave_birth_by_time = {}
    eng.wave_debug = {}
    eng._ext1_protection_per_bar = [0] * 20
    eng.cfg = type("C", (), {"ext1_protect_positions_until_wave2": False})()
    eng.open_trades = [_wave_trade_with_tp()]
    eng.closed_trades = []
    eng._append_closed_trade = lambda ct, _t: eng.closed_trades.append(ct)
    eng._make_closed = lambda trade, bar_idx, close_price, bar_time, reason: type(
        "CT", (), {"trade": trade, "reason": reason}
    )()

    eng._check_sl_tp(
        8,
        datetime(2026, 5, 1, 12, 0),
        high=1.1410,
        low=1.1290,
    )

    assert eng.open_trades == []
    assert eng.closed_trades[0].reason == "TP"
