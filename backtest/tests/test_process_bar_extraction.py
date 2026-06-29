"""1E — testy k akci 1D: legacy regrese == baseline 1A (closed_trades fingerprint).

Silnejsi nez agregat z 1A: krome poctu WAVE obchodu a net PnL zamykame
deterministicky otisk cele sekvence closed_trades a shodu run() s
prepare() + process_bar() smyckou (refaktor 1D).
"""
from __future__ import annotations

import hashlib
from typing import List, Sequence, Tuple

import pytest

from backtest.engine import BacktestEngine, ClosedTrade
from backtest.executor import BacktestExecutor
from backtest.grid.data_cache import clear_cache, load_data
from backtest.stats import compute_stats, trades_to_df
from backtest.wave_sim_cache import clear_pine_sim_cache
from config.bot_config import LIVE_BOT_CONFIG
from config.position_modes import resolve_grid_engine_config

DATE_FROM = "2025-11-10"
DATE_TO = "2026-05-09"

# Golden baseline — WAVE slice (compute_stats, track_concurrent=True)
EXPECTED_TRADES_WAVE = 164
EXPECTED_NET_PNL_WAVE_USD = 39040.88

# Fingerprint vsech 226 closed_trades (serazeno close_time, entry_time, wave_time).
# Naměřeno na varianta-a-faze-e po commitu 1D (33bae00).
EXPECTED_CLOSED_TRADES_COUNT = 226
EXPECTED_CLOSED_TRADES_SHA256 = (
    "43dc9bb77511943b7c3ba2a080e9a3df919ff109829a584fd50e3dbe62e5e0f6"
)

ClosedTradeRow = Tuple[str, str, int, float, float, str, str]


def _closed_trade_rows(trades: Sequence[ClosedTrade]) -> List[ClosedTradeRow]:
    rows: List[ClosedTradeRow] = []
    for t in sorted(trades, key=lambda x: (x.close_time, x.entry_time, x.wave_time)):
        rows.append(
            (
                str(t.entry_time),
                str(t.close_time),
                int(t.dir),
                round(float(t.lot), 2),
                round(float(t.pnl_usd), 2),
                str(t.wave_time),
                str(t.close_reason),
            )
        )
    return rows


def _closed_trades_fingerprint(trades: Sequence[ClosedTrade]) -> Tuple[str, int]:
    rows = _closed_trade_rows(trades)
    digest = hashlib.sha256(repr(rows).encode()).hexdigest()
    return digest, len(rows)


def _run_legacy_backtest() -> tuple[BacktestEngine, List[ClosedTrade]]:
    cfg = resolve_grid_engine_config(
        LIVE_BOT_CONFIG,
        date_from=DATE_FROM,
        date_to=DATE_TO,
    )
    assert getattr(cfg, "wave_detection_mode", "legacy_precompute") == "legacy_precompute"

    df = load_data(
        cfg.symbol,
        cfg.timeframe_label,
        DATE_FROM,
        DATE_TO,
    )
    assert not df.empty

    engine = BacktestEngine(cfg)
    trades = engine.run(df)
    return engine, trades


def _run_prepare_process_bar_loop(df) -> tuple[BacktestEngine, List[ClosedTrade]]:
    """Replikuje telo run() po prepare — overuje extrakci process_bar z 1D."""
    cfg = resolve_grid_engine_config(
        LIVE_BOT_CONFIG,
        date_from=DATE_FROM,
        date_to=DATE_TO,
    )
    engine = BacktestEngine(cfg)
    ctx = engine.prepare(df)
    executor = BacktestExecutor(engine)
    engine._executor = executor

    for i in range(1, ctx.ohlc.n):
        engine.process_bar(i, ctx, executor)

    last_ix = len(df) - 1
    engine._close_remaining(last_ix, df)
    return engine, list(engine.closed_trades)


@pytest.fixture(autouse=True)
def _reset_caches():
    clear_cache()
    clear_pine_sim_cache()
    yield
    clear_cache()
    clear_pine_sim_cache()


def test_legacy_closed_trades_fingerprint_matches_1a_baseline():
    _, trades = _run_legacy_backtest()
    stats = compute_stats(trades_to_df(trades), track_concurrent=True)

    assert stats["trades_wave"] == EXPECTED_TRADES_WAVE
    assert stats["net_pnl_wave_usd"] == EXPECTED_NET_PNL_WAVE_USD

    digest, count = _closed_trades_fingerprint(trades)
    assert count == EXPECTED_CLOSED_TRADES_COUNT
    assert digest == EXPECTED_CLOSED_TRADES_SHA256


def test_run_equals_prepare_process_bar_closed_trades():
    cfg = resolve_grid_engine_config(
        LIVE_BOT_CONFIG,
        date_from=DATE_FROM,
        date_to=DATE_TO,
    )
    df = load_data(cfg.symbol, cfg.timeframe_label, DATE_FROM, DATE_TO)

    trades_run = BacktestEngine(cfg).run(df.copy())
    _, trades_manual = _run_prepare_process_bar_loop(df.copy())

    assert _closed_trade_rows(trades_run) == _closed_trade_rows(trades_manual)
