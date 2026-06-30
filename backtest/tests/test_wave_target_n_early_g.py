from __future__ import annotations

from datetime import datetime

import pytest

from backtest.engine import BacktestEngine, OpenTrade, PendingOrder
from backtest.grid.translator import grid_dict_to_bot_config
from config.enums import TPMode
from strategy.wave_sequence import WaveSequenceInfo
from strategy.wave_target_n_early import (
    FormingTpWatch,
    extension_tp_hit_on_bar,
    g_counter_wave_time,
    start_forming_tp_watch,
    wave_target_n_early_g_enabled,
)
from strategy.wave_sequence import is_wave_counter_trade


def _base_grid(**overrides):
    d = {
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
        "tp_mode": "wave_target_n",
        "tp_target_wave_index": 4,
        "wave_extension_pct": 0.20,
    }
    d.update(overrides)
    return grid_dict_to_bot_config(d)


def _cfg_g(**overrides):
    return _base_grid(
        tp_wave_early_mode="forming_qualified",
        tp_wave_exit_on="extension_hit",
        tp_wave_early_fallback_birth=True,
        tp_wave_intrabar_priority="tp_before_sl",
        **overrides,
    )


def _armed_watch(*, trend_dir: int, armed_tp: float) -> FormingTpWatch:
    if trend_dir == -1:
        pivot, extreme = 1.1000, 1.0940
    else:
        pivot, extreme = 1.1000, 1.1150
    return FormingTpWatch(
        trend_dir=trend_dir,
        prev_wave={
            "wave_time": "w3",
            "dir": trend_dir,
            "box_top": 1.1100,
            "box_bottom": 1.1000,
            "move_pct": 0.68,
        },
        target_tp_index=4,
        start_bar=10,
        pivot=pivot,
        extreme=extreme,
        armed=True,
        armed_tp=float(armed_tp),
    )


def _short_trade(*, entry_bar: int = 5) -> OpenTrade:
    po = PendingOrder(
        signal={"wave_time": "W2", "dir": -1},
        order_type="SELL_LIMIT",
        entry_price=1.1080,
        sl=1.1150,
        tp=None,
        lot=0.1,
        created_bar=4,
        created_time=datetime(2026, 5, 1, 9, 0),
        dir_override=-1,
    )
    return OpenTrade(
        po, entry_bar, 1.1080, datetime(2026, 5, 1, 10, 0), "LIMIT", 1.1150, None,
    )


def _long_trade(*, entry_bar: int = 5) -> OpenTrade:
    po = PendingOrder(
        signal={"wave_time": "W2", "dir": 1},
        order_type="BUY_LIMIT",
        entry_price=1.1020,
        sl=1.0950,
        tp=None,
        lot=0.1,
        created_bar=4,
        created_time=datetime(2026, 5, 1, 9, 0),
        dir_override=1,
    )
    return OpenTrade(
        po, entry_bar, 1.1020, datetime(2026, 5, 1, 10, 0), "LIMIT", 1.0950, None,
    )


def test_g_cfg_enabled_only_for_wave_target_n():
    cfg = _cfg_g()
    assert wave_target_n_early_g_enabled(cfg) is True
    cfg_rrr = _base_grid(tp_mode="rrr_fixed", tp_wave_early_mode="forming_qualified")
    assert wave_target_n_early_g_enabled(cfg_rrr) is False


def test_start_forming_tp_watch_after_w3():
    w3 = {"wave_time": "w3", "dir": -1, "box_top": 1.1100, "box_bottom": 1.1000}
    watch = start_forming_tp_watch(
        prev_wave=w3, index_in_trend=3, target_n=4, start_bar=10,
    )
    assert watch is not None
    assert watch.trend_dir == -1
    assert watch.target_tp_index == 4


@pytest.mark.parametrize(
    ("high", "low", "close", "open_", "expected"),
    [
        (1.0990, 1.0975, 1.0978, 1.0985, True),
        (1.1120, 1.0970, 1.1110, 1.0975, False),
    ],
)
def test_bear_extension_hit_descent_not_spike(high, low, close, open_, expected):
    watch = _armed_watch(trend_dir=-1, armed_tp=1.0980)
    assert extension_tp_hit_on_bar(
        watch, high=high, low=low, close=close, open_=open_,
    ) is expected


@pytest.mark.parametrize(
    ("high", "low", "close", "open_", "expected"),
    [
        (1.1125, 1.1110, 1.1122, 1.1110, True),
        (1.1125, 1.1110, 1.1110, 1.1122, False),
    ],
)
def test_bull_extension_hit_ascent_not_reversal(high, low, close, open_, expected):
    watch = _armed_watch(trend_dir=1, armed_tp=1.1120)
    assert extension_tp_hit_on_bar(
        watch, high=high, low=low, close=close, open_=open_,
    ) is expected


def test_forming_watch_arms_extension_tp_from_w3():
    cfg = _cfg_g()
    w3 = {"wave_time": "w3", "dir": -1, "box_top": 1.1100, "box_bottom": 1.1000}
    watch = start_forming_tp_watch(
        prev_wave=w3, index_in_trend=3, target_n=4, start_bar=10,
    )
    assert watch is not None
    watch.update_extreme(1.1090, 1.0940)
    assert watch.try_arm(cfg) is True
    assert watch.armed_tp == pytest.approx(1.0980)


def test_engine_bear_extension_hit_closes_short():
    cfg = _cfg_g()
    eng = BacktestEngine(cfg)
    eng.open_trades = [_short_trade()]
    w3 = {"wave_time": "w3", "dir": -1, "box_top": 1.1100, "box_bottom": 1.1000}
    eng.wave_sequence_info = {"w3": WaveSequenceInfo(3, "w1")}
    eng._on_wave_born_forming_tp_context(w3, 10)
    eng._update_forming_tp_watch_on_bar(1.1090, 1.0940)
    assert eng._forming_tp_watch is not None
    assert eng._forming_tp_watch.armed is True

    eng._maybe_fire_extension_tp_on_bar(
        11,
        datetime(2026, 5, 1, 11, 0),
        1.0985,
        1.0990,
        1.0975,
        1.0978,
    )
    assert eng.open_trades == []
    assert len(eng.closed_trades) == 1
    assert eng.closed_trades[0].close_reason == "TP_EXTENSION_HIT"
    assert eng.closed_trades[0].close_price == pytest.approx(1.0980)


def test_g_counter_wave_time_fits_mt5_comment():
    watch = _armed_watch(trend_dir=-1, armed_tp=1.0980)
    key = g_counter_wave_time(watch)
    assert len(f"CNTR_{key}") <= 31
    assert key.endswith("@G4")


def test_g_counter_wave_time_truncates_long_prev_wave_time():
    watch = _armed_watch(trend_dir=-1, armed_tp=1.0980)
    watch.prev_wave["wave_time"] = "x" * 40
    key = g_counter_wave_time(watch)
    assert len(f"CNTR_{key}") <= 31
    assert "@G4" in key


def test_engine_g_extension_hit_opens_wave_counter_market():
    cfg = _cfg_g(counter_position_enabled=True)
    eng = BacktestEngine(cfg)
    eng.open_trades = [_short_trade()]
    w3 = {
        "wave_time": "w3",
        "dir": -1,
        "box_top": 1.1100,
        "box_bottom": 1.1000,
        "move_pct": 0.68,
    }
    eng.wave_sequence_info = {"w3": WaveSequenceInfo(3, "w1")}
    eng._on_wave_born_forming_tp_context(w3, 10)
    eng._update_forming_tp_watch_on_bar(1.1090, 1.0940)
    assert eng._forming_tp_watch is not None
    assert eng._forming_tp_watch.armed is True

    eng._maybe_fire_extension_tp_on_bar(
        11,
        datetime(2026, 5, 1, 11, 0),
        1.0985,
        1.0990,
        1.0975,
        1.0978,
    )
    assert len(eng.closed_trades) == 1
    assert len(eng.open_trades) == 1
    counter = eng.open_trades[0]
    assert is_wave_counter_trade(counter)
    assert counter.entry_tag == "wave_counter"
    assert counter.entry_type == "MARKET"
    assert counter.actual_entry == pytest.approx(1.0980)
    assert eng._forming_tp_watch.counter_placed is True
    assert eng.pending_orders == []


def test_engine_g_skips_counter_on_birth_after_extension_hit():
    cfg = _cfg_g(counter_position_enabled=True)
    eng = BacktestEngine(cfg)
    eng.open_trades = [_short_trade()]
    w3 = {
        "wave_time": "w3",
        "dir": -1,
        "box_top": 1.1100,
        "box_bottom": 1.1000,
        "move_pct": 0.68,
    }
    eng.wave_sequence_info = {
        "w3": WaveSequenceInfo(3, "w1"),
        "w4": WaveSequenceInfo(4, "w3"),
    }
    eng.waves_by_wave_time = {"w3": w3, "w4": {"wave_time": "w4", "dir": -1}}
    eng._on_wave_born_forming_tp_context(w3, 10)
    eng._update_forming_tp_watch_on_bar(1.1090, 1.0940)
    eng._maybe_fire_extension_tp_on_bar(
        11, datetime(2026, 5, 1, 11, 0), 1.0985, 1.0990, 1.0975, 1.0978,
    )
    assert len(eng.open_trades) == 1

    w4 = {
        "wave_time": "w4",
        "dir": -1,
        "box_top": 1.1100,
        "box_bottom": 1.0940,
        "wave_target_tp_price": 1.0980,
    }
    eng._maybe_place_counter_from_tp(
        w4, 1.0980, 15, datetime(2026, 5, 1, 15, 0),
    )
    assert len(eng.open_trades) == 1
    assert eng.pending_orders == []


def test_engine_bull_extension_hit_closes_long():
    cfg = _cfg_g()
    eng = BacktestEngine(cfg)
    eng.open_trades = [_long_trade()]
    w3 = {"wave_time": "w3", "dir": 1, "box_top": 1.1100, "box_bottom": 1.1000}
    eng.wave_sequence_info = {"w3": WaveSequenceInfo(3, "w1")}
    eng._on_wave_born_forming_tp_context(w3, 10)
    eng._update_forming_tp_watch_on_bar(1.1150, 1.1090)
    assert eng._forming_tp_watch.armed is True

    eng._maybe_fire_extension_tp_on_bar(
        11,
        datetime(2026, 5, 1, 11, 0),
        1.1110,
        1.1125,
        1.1110,
        1.1122,
    )
    assert eng.open_trades == []
    assert eng.closed_trades[0].close_reason == "TP_EXTENSION_HIT"


def test_engine_fallback_tp_wave_n_on_birth_when_no_extension_hit():
    cfg = _cfg_g(counter_position_enabled=True)
    eng = BacktestEngine(cfg)
    eng.open_trades = [_short_trade(entry_bar=8)]
    w3 = {
        "wave_time": "w3",
        "dir": -1,
        "box_top": 1.1100,
        "box_bottom": 1.1000,
        "move_pct": 0.68,
    }
    eng.wave_sequence_info = {
        "w3": WaveSequenceInfo(3, "w1"),
        "w4": WaveSequenceInfo(4, "w3"),
    }
    eng.waves_by_wave_time = {"w3": w3}
    eng._on_wave_born_forming_tp_context(w3, 10)
    eng._update_forming_tp_watch_on_bar(1.1090, 1.1050)
    assert eng._forming_tp_watch.armed is False

    w4 = {
        "wave_time": "w4",
        "dir": -1,
        "box_top": 1.1100,
        "box_bottom": 1.0950,
        "wave_target_tp_price": 1.0980,
    }
    eng.waves_by_wave_time["w4"] = w4
    eng._maybe_fire_tp_wave_event(
        w4,
        15,
        datetime(2026, 5, 1, 15, 0),
        1.1000,
        1.1010,
        1.0990,
    )
    assert eng.open_trades == []
    assert eng.closed_trades[0].close_reason == "TP_WAVE_N"
    assert len(eng.pending_orders) == 1
    assert eng.pending_orders[0].is_counter is True
    assert eng.pending_orders[0].entry_tag == "wave_counter"


def test_engine_skips_birth_after_extension_hit():
    cfg = _cfg_g()
    eng = BacktestEngine(cfg)
    eng.open_trades = [_short_trade()]
    w3 = {"wave_time": "w3", "dir": -1, "box_top": 1.1100, "box_bottom": 1.1000}
    eng.wave_sequence_info = {
        "w3": WaveSequenceInfo(3, "w1"),
        "w4": WaveSequenceInfo(4, "w3"),
    }
    eng._on_wave_born_forming_tp_context(w3, 10)
    eng._update_forming_tp_watch_on_bar(1.1090, 1.0940)
    eng._maybe_fire_extension_tp_on_bar(
        11, datetime(2026, 5, 1, 11, 0), 1.0985, 1.0990, 1.0975, 1.0978,
    )
    assert eng.open_trades == []

    w4 = {
        "wave_time": "w4",
        "dir": -1,
        "box_top": 1.1100,
        "box_bottom": 1.0940,
    }
    eng._maybe_fire_tp_wave_event(
        w4, 15, datetime(2026, 5, 1, 15, 0), 1.1000, 1.1010, 1.0990,
    )
    assert len(eng.closed_trades) == 1


def test_wave_target_n_g_skips_counter_from_wave_entry():
    cfg = _cfg_g(counter_position_enabled=True)
    eng = BacktestEngine(cfg)
    wave = {
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
    eng.waves_by_wave_time = {"curr": wave, "prev": prev_wave}
    eng._maybe_place_counter_from_tp(
        wave=wave,
        tp_price=1.1300,
        bar_idx=10,
        bar_time=datetime(2026, 5, 1, 10, 0),
    )
    assert eng.pending_orders == []


def test_off_mode_no_extension_close():
    cfg = _base_grid(tp_wave_early_mode="off")
    eng = BacktestEngine(cfg)
    assert eng._tp_mode == TPMode.WAVE_TARGET_N
    eng.open_trades = [_short_trade()]
    w3 = {"wave_time": "w3", "dir": -1, "box_top": 1.1100, "box_bottom": 1.1000}
    eng.wave_sequence_info = {"w3": WaveSequenceInfo(3, "w1")}
    eng._on_wave_born_forming_tp_context(w3, 10)
    assert eng._forming_tp_watch is None

    eng._maybe_fire_extension_tp_on_bar(
        11, datetime(2026, 5, 1, 11, 0), 1.0985, 1.0990, 1.0975, 1.0978,
    )
    assert len(eng.open_trades) == 1
