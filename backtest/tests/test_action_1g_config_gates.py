"""1G — config-gated exit timing + session pre-close cancel (default OFF)."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from unittest.mock import MagicMock

from backtest.engine import BacktestEngine, PendingOrder
from backtest.executor import BacktestExecutor
from backtest.grid.translator import grid_dict_to_bot_config
from config.bot_config import LIVE_BOT_CONFIG
from config.position_modes import resolve_grid_engine_config


def _cfg(**overrides) -> dict:
    base = dict(
        timeframe="M30",
        wave_min_pct=0.26,
        min_opp_bars=3,
        rrr=2.0,
        fib_level=0.5,
        entry_mode="market_fallback",
        symbol="EURUSD.x",
        sl_fib_level=0.8,
        wave_plus=True,
        risk_usd=500.0,
        contract_size=100_000.0,
        tp_mode="wave_target_n",
        tp_target_wave_index=4,
    )
    base.update(overrides)
    return grid_dict_to_bot_config(base)


def _make_pending(wave_time: str, *, created_time: datetime) -> PendingOrder:
    sig = {"wave_time": wave_time, "dir": 1, "fib50": 1.1, "sl": 1.099}
    return PendingOrder(
        signal=sig,
        order_type="BUY_LIMIT",
        entry_price=1.1,
        sl=1.099,
        tp=None,
        lot=0.01,
        created_bar=0,
        created_time=created_time,
    )


def test_1g_flags_default_off_in_live_grid_config():
    cfg = resolve_grid_engine_config(LIVE_BOT_CONFIG)
    assert getattr(cfg, "backtest_tp_wave_before_bos_same_bar", False) is False
    assert getattr(cfg, "backtest_model_session_pre_close_cancel", False) is False


def test_session_pre_close_cancel_removes_pendings_once_per_day():
    cfg = replace(
        _cfg(),
        session_enabled=True,
        session_close_time="23:45",
        session_pre_close_buffer_min=5,
        session_weekdays_only=False,
        backtest_model_session_pre_close_cancel=True,
    )
    eng = BacktestEngine(cfg)
    eng._executor = BacktestExecutor(eng)
    pending = _make_pending("wt1", created_time=datetime(2026, 6, 17, 10, 0))
    eng.pending_orders = [pending]

    in_buffer = datetime(2026, 6, 17, 23, 42)
    eng._maybe_model_session_pre_close_cancel(1, in_buffer, eng._executor)
    assert pending not in eng.pending_orders
    assert eng.wave_debug.get("session_pre_close_pendings_cancelled", 0) == 1

    pending2 = _make_pending("wt2", created_time=datetime(2026, 6, 17, 23, 41))
    eng.pending_orders = [pending2]
    eng._maybe_model_session_pre_close_cancel(2, datetime(2026, 6, 17, 23, 43), eng._executor)
    assert pending2 in eng.pending_orders

    out_of_buffer = datetime(2026, 6, 18, 23, 30)
    pending3 = _make_pending("wt3", created_time=datetime(2026, 6, 18, 10, 0))
    eng.pending_orders = [pending3]
    eng._maybe_model_session_pre_close_cancel(3, out_of_buffer, eng._executor)
    assert pending3 in eng.pending_orders


def test_session_pre_close_cancel_noop_when_flag_off():
    cfg = replace(
        _cfg(),
        session_enabled=True,
        session_close_time="23:45",
        session_pre_close_buffer_min=5,
        backtest_model_session_pre_close_cancel=False,
    )
    eng = BacktestEngine(cfg)
    eng._executor = BacktestExecutor(eng)
    pending = _make_pending("wt1", created_time=datetime(2026, 6, 17, 10, 0))
    eng.pending_orders = [pending]
    eng._maybe_model_session_pre_close_cancel(
        1, datetime(2026, 6, 17, 23, 42), eng._executor,
    )
    assert pending in eng.pending_orders


def test_tp_wave_before_bos_reorders_on_same_bar():
    cfg = replace(_cfg(), backtest_tp_wave_before_bos_same_bar=True)
    eng = BacktestEngine(cfg)
    eng.wave_sequence_info = {
        "wt_tp": MagicMock(index_in_trend=4),
    }
    calls: list[str] = []

    def _bos(*_a, **_k):
        calls.append("bos")

    def _tp(*_a, **_k):
        calls.append("tp")

    eng._run_bos_exit_block = _bos  # type: ignore[method-assign]
    eng._run_tp_wave_events_on_bar = _tp  # type: ignore[method-assign]

    new_waves = [{"wave_time": "wt_tp", "dir": 1}]
    assert eng._bar_has_tp_wave_n_birth(new_waves) is True

    tp_before_bos = bool(
        getattr(cfg, "backtest_tp_wave_before_bos_same_bar", False)
    ) and eng._bar_has_tp_wave_n_birth(new_waves)
    assert tp_before_bos is True

    if tp_before_bos:
        eng._run_tp_wave_events_on_bar(new_waves, 1, datetime(2026, 1, 1), 1.0, 1.0, 1.0)
        eng._run_bos_exit_block(1, datetime(2026, 1, 1), 1.0, 1.0, 1.0, set())
    else:
        eng._run_bos_exit_block(1, datetime(2026, 1, 1), 1.0, 1.0, 1.0, set())
        eng._run_tp_wave_events_on_bar(new_waves, 1, datetime(2026, 1, 1), 1.0, 1.0, 1.0)

    assert calls == ["tp", "bos"]


def test_bar_has_tp_wave_n_birth_false_for_non_target_index():
    cfg = _cfg(tp_target_wave_index=4)
    eng = BacktestEngine(cfg)
    eng.wave_sequence_info = {
        "wt2": MagicMock(index_in_trend=2),
    }
    assert eng._bar_has_tp_wave_n_birth([{"wave_time": "wt2", "dir": 1}]) is False
