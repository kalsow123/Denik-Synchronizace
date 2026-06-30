from __future__ import annotations

from datetime import datetime

import pytest

from backtest.engine import BacktestEngine, PendingOrder
from backtest.grid.translator import grid_dict_to_bot_config
from strategy.trend_bos import resolve_effective_tp


def _cfg():
    return grid_dict_to_bot_config(
        {
            "timeframe": "M30",
            "wave_min_pct": 0.26,
            "min_opp_bars": 3,
            "rrr": 2.0,
            "fib_level": 0.5,
            "entry_mode": "market_fallback",
            "symbol": "EURUSD",
            "sl_fib_level": 0.8,
            "wave_plus": True,
            "risk_usd": 500.0,
            "contract_size": 100_000.0,
            "tp_mode": "bos_exit",
            "pp_sl_pct": 0.21,
            "pp_risk_usd": 500.0,
        }
    )


def _trigger_fill(eng: BacktestEngine, po: PendingOrder, *, fill_price: float):
    eng.pending_orders = [po]
    eng.open_trades = []
    eng.trend_states_per_bar = [
        type("S", (), {"direction": "bull"})() for _ in range(20)
    ]
    if getattr(po, "is_pp", False):
        eng._pp_trend_confirmed_per_bar = [True] * 20
        eng._pp_current_pending = None
    eng._trigger_pending(
        bar_idx=11,
        bar_time=datetime(2026, 5, 1, 11, 0),
        high=fill_price + 0.0005,
        low=fill_price - 0.0005,
        open_=fill_price,
    )


def test_pp_tp_recalc_on_fill_from_actual_entry():
    cfg = _cfg()
    eng = BacktestEngine(cfg)
    signal = {
        "wave_time": "pp1",
        "dir": 1,
        "fib50": 1.1200,
        "sl": 1.1150,
        "box_top": 1.1250,
        "box_bottom": 1.1150,
    }
    entry = 1.1250
    sl = 1.122625
    fill_price = 1.1248
    po = PendingOrder(
        signal=signal,
        order_type="BUY_LIMIT",
        entry_price=entry,
        sl=sl,
        tp=resolve_effective_tp(cfg, signal, entry, sl, is_buy=True),
        lot=0.1,
        created_bar=10,
        created_time=datetime(2026, 5, 1, 10, 0),
        dir_override=1,
        is_pp=True,
    )
    _trigger_fill(eng, po, fill_price=fill_price)

    assert len(eng.open_trades) == 1
    trade = eng.open_trades[0]
    slipped = fill_price + eng.backtest_slippage
    expected = resolve_effective_tp(cfg, signal, slipped, sl, is_buy=True)
    assert trade.tp == pytest.approx(expected)
    if po.tp is not None and expected is not None:
        assert trade.tp != pytest.approx(po.tp) or fill_price == po.entry_price


def test_two_sided_tp_recalc_on_fill_from_actual_entry():
    cfg = _cfg()
    eng = BacktestEngine(cfg)
    signal = {
        "wave_time": "ts1",
        "dir": -1,
        "fib50": 1.1300,
        "sl": 1.1350,
        "box_top": 1.1350,
        "box_bottom": 1.1200,
    }
    entry = 1.1300
    sl = 1.1350
    fill_price = 1.1302
    po = PendingOrder(
        signal=signal,
        order_type="SELL_LIMIT",
        entry_price=entry,
        sl=sl,
        tp=resolve_effective_tp(cfg, signal, entry, sl, is_buy=False),
        lot=0.1,
        created_bar=10,
        created_time=datetime(2026, 5, 1, 10, 0),
        dir_override=-1,
        is_two_sided_mirror=True,
    )
    _trigger_fill(eng, po, fill_price=fill_price)

    assert len(eng.open_trades) == 1
    trade = eng.open_trades[0]
    slipped = fill_price - eng.backtest_slippage
    expected = resolve_effective_tp(cfg, signal, slipped, sl, is_buy=False)
    assert trade.tp == pytest.approx(expected)
