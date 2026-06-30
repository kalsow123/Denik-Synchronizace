from __future__ import annotations

from datetime import datetime

import pytest

from backtest.engine import BacktestEngine, OpenTrade, PendingOrder
from backtest.grid.translator import grid_dict_to_bot_config
from config.enums import TPMode
from strategy.wave_sequence import (
    WaveSequenceInfo,
    compute_ladder_sl_from_wave_size,
    compute_wave_counter_take_profit,
    wave_counter_min_sl_pct,
)


def _cfg(tp_mode: str):
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
            "counter_position_enabled": True,
            "tp_mode": tp_mode,
            "tp_target_wave_index": 4,
            "wave_extension_pct": 0.20,
            "wave_size_sl_ladder_base_pct": 0.21,
            "wave_size_sl_ladder_step_pct": 0.16,
            "wave_size_sl_ladder_band_size_pct": 0.50,
        }
    )


def _counter_setup(eng: BacktestEngine, tp_mode: str):
    wave_tp = {
        "wave_time": "curr",
        "dir": 1,
        "fib50": 1.1200,
        "sl": 1.1100,
        "box_bottom": 1.1150,
        "box_top": 1.1250,
        "wave_target_tp_price": 1.1300,
    }
    prev_wave = {"wave_time": "prev", "move_pct": 0.68}
    eng.wave_sequence_info = {"curr": WaveSequenceInfo(4, "prev")}
    eng.waves_by_wave_time = {"curr": wave_tp, "prev": prev_wave}
    eng._maybe_place_counter_from_tp(
        wave=wave_tp,
        tp_price=1.1300 if tp_mode != "wave_target_n" else None,
        bar_idx=10,
        bar_time=datetime(2026, 5, 1, 10, 0),
    )
    return wave_tp, prev_wave


@pytest.mark.parametrize("tp_mode", ["rrr_fixed", "bos_exit", "wave_target_n"])
def test_wave_counter_only_on_tp_wave_index(tp_mode: str):
    """Vsechny tp_mode: counter jen na TP-vlne (N, N+2, ...), ne na K < N."""
    cfg = _cfg(tp_mode)
    eng = BacktestEngine(cfg)
    wave = {"wave_time": "curr", "dir": 1, "fib50": 1.1234, "sl": 1.1200}
    prev_wave = {"wave_time": "prev", "move_pct": 0.68}
    eng.wave_sequence_info = {
        "curr": WaveSequenceInfo(2, "prev"),
        "tp4": WaveSequenceInfo(4, "prev"),
    }
    eng.waves_by_wave_time = {"curr": wave, "prev": prev_wave}

    eng._maybe_place_counter_from_tp(
        wave=wave,
        tp_price=1.1234,
        bar_idx=10,
        bar_time=datetime(2026, 5, 1, 10, 0),
    )
    assert eng.pending_orders == []

    _counter_setup(eng, tp_mode)

    assert len(eng.pending_orders) == 1
    order = eng.pending_orders[0]
    assert order.is_counter is True
    assert order.entry_tag == "wave_counter"
    assert order.entry_price == pytest.approx(1.1300)
    if tp_mode in ("wave_target_n", "bos_exit"):
        assert order.tp is None
    else:
        assert order.tp is not None
        expected = compute_wave_counter_take_profit(
            cfg, order.entry_price, order.sl, is_buy=(order.dir == 1)
        )
        assert order.tp == pytest.approx(expected)


def test_wave_target_n_computes_extension_tp_when_precomputed_missing():
    """TP-vlna bez wave_target_tp_price v dict — engine dopočítá extension TP."""
    cfg = _cfg("wave_target_n")
    eng = BacktestEngine(cfg)
    wave = {
        "wave_time": "curr",
        "dir": 1,
        "fib50": 1.1200,
        "sl": 1.1100,
        "box_bottom": 1.1150,
        "box_top": 1.1250,
    }
    prev_wave = {
        "wave_time": "prev",
        "move_pct": 0.68,
        "box_bottom": 1.1000,
        "box_top": 1.1100,
    }
    eng.wave_sequence_info = {"curr": WaveSequenceInfo(4, "prev")}
    eng.waves_by_wave_time = {"curr": wave, "prev": prev_wave}

    eng._maybe_place_counter_from_tp(
        wave=wave,
        tp_price=None,
        bar_idx=10,
        bar_time=datetime(2026, 5, 1, 10, 0),
    )

    assert len(eng.pending_orders) == 1
    assert eng.pending_orders[0].entry_price == pytest.approx(1.1170)
    assert eng.pending_orders[0].tp is None


@pytest.mark.parametrize("tp_mode", ["rrr_fixed", "bos_exit"])
def test_wave_counter_rrr_tp_on_fill(tp_mode: str):
    """Po fillu wave counter ma RRR TP prepocitany ze skutecne entry a SL."""
    cfg = _cfg(tp_mode)
    eng = BacktestEngine(cfg)
    _counter_setup(eng, tp_mode)
    order = eng.pending_orders[0]
    eng.pending_orders = []
    eng.open_trades = []

    bar_time = datetime(2026, 5, 1, 11, 0)
    po = PendingOrder(
        signal={"wave_time": "curr", "dir": order.dir},
        order_type=order.order_type,
        entry_price=order.entry_price,
        sl=order.sl,
        tp=order.tp,
        lot=order.lot,
        created_bar=10,
        created_time=bar_time,
        dir_override=order.dir,
        is_counter=True,
        entry_tag="wave_counter",
    )
    eng.pending_orders = [po]
    eng.trend_states_per_bar = [
        type("S", (), {"direction": "bull"})() for _ in range(12)
    ]
    eng._trigger_pending(
        bar_idx=11,
        bar_time=bar_time,
        high=po.entry_price + 0.001,
        low=po.entry_price - 0.001,
        open_=po.entry_price,
    )

    assert len(eng.open_trades) == 1
    trade = eng.open_trades[0]
    expected = compute_wave_counter_take_profit(
        cfg, trade.actual_entry, trade.sl, is_buy=(trade.dir == 1)
    )
    assert trade.tp == pytest.approx(expected)


def test_tp_wave_event_does_not_close_wave_counter():
    """WAVE_TARGET_N: TP-vlna NEZAVRE otevreny wave counter (ten jede s novym trendem)."""
    cfg = _cfg("wave_target_n")
    eng = BacktestEngine(cfg)
    eng._tp_mode = TPMode.WAVE_TARGET_N
    eng.wave_sequence_info = {"curr": WaveSequenceInfo(4, "prev")}

    _, prev_wave = _counter_setup(eng, "wave_target_n")
    order = eng.pending_orders[0]
    _, counter_sl = compute_ladder_sl_from_wave_size(
        order.entry_price,
        float(prev_wave["move_pct"]),
        cfg,
        is_buy=(order.dir == 1),
        min_sl_pct=wave_counter_min_sl_pct(cfg),
    )

    po = PendingOrder(
        signal={"wave_time": "curr", "dir": order.dir},
        order_type=order.order_type,
        entry_price=order.entry_price,
        sl=counter_sl,
        tp=None,
        lot=order.lot,
        created_bar=9,
        created_time=datetime(2026, 5, 1, 9, 0),
        dir_override=order.dir,
        is_counter=True,
        entry_tag="wave_counter",
    )
    eng.open_trades = [
        OpenTrade(
            po, 9, order.entry_price, datetime(2026, 5, 1, 9, 30), "LIMIT", counter_sl, None
        )
    ]
    eng.pending_orders = []

    wave = eng.waves_by_wave_time["curr"]
    bar_time = datetime(2026, 5, 1, 10, 0)
    eng._maybe_fire_tp_wave_event(
        wave=wave,
        bar_idx=10,
        bar_time=bar_time,
        bar_close=1.1280,
        bar_high=1.1290,
        bar_low=1.1270,
    )

    assert len(eng.open_trades) == 1
    assert len(eng.closed_trades) == 0
    assert eng.open_trades[0].entry_tag == "wave_counter"
