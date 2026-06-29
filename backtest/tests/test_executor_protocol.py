"""1E — executor protocol gap-check (akce 1D).

Overuje, ze rozhrani Executor/BacktestExecutor neni prilis uzke pro
mechaniku z VARIANTA A.txt §3.4 (TS2 lot mirror, position-cap prune,
EXT-1 SL ochrana pres fill model, two-sided promote/TP clear protokol).
"""
from __future__ import annotations

import inspect
from collections import Counter

import pytest

from backtest.engine import BacktestEngine
from backtest.executor import BacktestExecutor, Executor
from backtest.grid.data_cache import clear_cache, load_data
from backtest.wave_sim_cache import clear_pine_sim_cache
from config.bot_config import LIVE_BOT_CONFIG
from config.position_modes import resolve_grid_engine_config

DATE_FROM = "2025-11-10"
DATE_TO = "2026-05-09"

REQUIRED_EXECUTOR_METHODS = (
    "place_pending",
    "place_market",
    "close_position",
    "cancel_pending",
    "modify_sltp",
    "close_partial",
    "modify_lot",
    "get_open_positions",
    "get_pendings",
    "on_bar_open",
    "on_bar_range",
)

BACKTEST_EXECUTOR_PROCESS_BAR_METHODS = (
    "prune_pendings",
    "enforce_overflow",
    "expire_pendings",
)


class RecordingExecutor(Executor):
    """Spy wrapper: deleguje na BacktestExecutor a pocita volani."""

    def __init__(self, inner: BacktestExecutor) -> None:
        self._inner = inner
        self.calls: Counter[str] = Counter()

    def _record(self, name: str) -> None:
        self.calls[name] += 1

    def place_pending(self, order, bar_idx, bar_time):
        self._record("place_pending")
        return self._inner.place_pending(order, bar_idx, bar_time)

    def place_market(self, trade, bar_idx, bar_time):
        self._record("place_market")
        return self._inner.place_market(trade, bar_idx, bar_time)

    def close_position(self, trade, *, reason, price, bar_idx, bar_time):
        self._record("close_position")
        return self._inner.close_position(
            trade,
            reason=reason,
            price=price,
            bar_idx=bar_idx,
            bar_time=bar_time,
        )

    def cancel_pending(self, order):
        self._record("cancel_pending")
        return self._inner.cancel_pending(order)

    def modify_sltp(self, trade, *, sl=None, tp=None):
        self._record("modify_sltp")
        return self._inner.modify_sltp(trade, sl=sl, tp=tp)

    def close_partial(self, trade, lot, *, reason, price, bar_idx, bar_time):
        self._record("close_partial")
        return self._inner.close_partial(
            trade,
            lot,
            reason=reason,
            price=price,
            bar_idx=bar_idx,
            bar_time=bar_time,
        )

    def modify_lot(self, trade, lot):
        self._record("modify_lot")
        return self._inner.modify_lot(trade, lot)

    def get_open_positions(self):
        self._record("get_open_positions")
        return self._inner.get_open_positions()

    def get_pendings(self):
        self._record("get_pendings")
        return self._inner.get_pendings()

    def on_bar_open(self, bar_idx, bar_time, high, low, open_):
        self._record("on_bar_open")
        return self._inner.on_bar_open(bar_idx, bar_time, high, low, open_)

    def on_bar_range(self, bar_idx, bar_time, high, low):
        self._record("on_bar_range")
        return self._inner.on_bar_range(bar_idx, bar_time, high, low)

    def prune_pendings(self, mid_price):
        self._record("prune_pendings")
        return self._inner.prune_pendings(mid_price)

    def enforce_overflow(self, bar_idx, bar_time, mid_price):
        self._record("enforce_overflow")
        return self._inner.enforce_overflow(bar_idx, bar_time, mid_price)

    def expire_pendings(self, bar_idx, bar_time):
        self._record("expire_pendings")
        return self._inner.expire_pendings(bar_idx, bar_time)


@pytest.fixture(autouse=True)
def _reset_caches():
    clear_cache()
    clear_pine_sim_cache()
    yield
    clear_cache()
    clear_pine_sim_cache()


def test_executor_abc_declares_required_methods():
    for name in REQUIRED_EXECUTOR_METHODS:
        assert hasattr(Executor, name), f"Executor chybi metoda {name}"
        fn = getattr(Executor, name)
        assert getattr(fn, "__isabstractmethod__", False), (
            f"Executor.{name} musi byt abstraktni"
        )


def test_backtest_executor_implements_all_abstract_methods():
    abstract_names = {
        name
        for name, value in inspect.getmembers(Executor)
        if getattr(value, "__isabstractmethod__", False)
    }
    assert abstract_names == set(REQUIRED_EXECUTOR_METHODS)

    engine = BacktestEngine.__new__(BacktestEngine)
    impl = BacktestExecutor(engine)
    for name in REQUIRED_EXECUTOR_METHODS:
        assert callable(getattr(impl, name))

    for name in BACKTEST_EXECUTOR_PROCESS_BAR_METHODS:
        assert callable(getattr(impl, name)), (
            f"BacktestExecutor chybi {name} pro process_bar gap-check"
        )


def test_gap_check_engine_routes_key_mechanics_through_executor():
    """Legacy beh se spy executorem — overi, ze 1D cesty nejsou bypass."""
    cfg = resolve_grid_engine_config(
        LIVE_BOT_CONFIG,
        date_from=DATE_FROM,
        date_to=DATE_TO,
    )
    df = load_data(cfg.symbol, cfg.timeframe_label, DATE_FROM, DATE_TO)
    engine = BacktestEngine(cfg)
    ctx = engine.prepare(df)
    inner = BacktestExecutor(engine)
    recorder = RecordingExecutor(inner)
    engine._executor = recorder

    for i in range(1, ctx.ohlc.n):
        engine.process_bar(i, ctx, recorder)

    calls = recorder.calls

    assert calls["place_pending"] > 0, "place_pending musi jit pres executor (TS2 lot mirror)"
    assert calls["prune_pendings"] > 0, "prune_pendings musi jit pres executor"
    assert calls["on_bar_open"] > 0, "on_bar_open musi jit pres executor"
    assert calls["on_bar_range"] > 0, "on_bar_range musi jit pres executor (EXT-1 SL)"
    assert calls["expire_pendings"] > 0, "expire_pendings musi jit pres executor"
    assert calls["enforce_overflow"] > 0, "enforce_overflow musi jit pres executor"

    assert engine.wave_debug.get("two_sided_mirror_accepted", 0) > 0
    assert not getattr(BacktestExecutor.modify_sltp, "__isabstractmethod__", False)


def test_backtest_executor_modify_sltp_supports_tp_clear():
    """BacktestExecutor.modify_sltp umozni tp=None (two-sided promote/TP clear)."""
    engine = BacktestEngine.__new__(BacktestEngine)
    ex = BacktestExecutor(engine)
    trade = type("T", (), {"sl": 1.0, "tp": 1.5})()

    ex.modify_sltp(trade, tp=None, _tp_set=True)
    assert trade.tp is None
