"""1E — testy k akci 1D: legacy regrese == baseline 1A (closed_trades fingerprint).

Silnejsi nez agregat z 1A: krome poctu WAVE obchodu a net PnL zamykame
deterministicky otisk cele sekvence closed_trades a shodu run() s
prepare() + process_bar() smyckou (refaktor 1D).

Re-baseline na 2leté okno (BACKTEST_WINDOW_YEARS=2); dříve 6měsíční
2025-11-10..2026-05-09 = 164/+39040.88 resp. 147/+38100.69 (fingerprint 226).
Okno = posledni 2 roky odvozene z datasetu (EURUSD M30 2024-05-20 .. 2026-05-18).
"""
from __future__ import annotations

import functools
import hashlib
from typing import List, Sequence, Tuple

import pytest

from backtest.data_loader import default_backtest_date_range, load_csv
from backtest.engine import BacktestEngine, ClosedTrade
from backtest.executor import BacktestExecutor
from backtest.grid.data_cache import clear_cache, csv_path_for, load_data
from backtest.stats import compute_stats, trades_to_df
from backtest.wave_sim_cache import clear_pine_sim_cache
from config.bot_config import LIVE_BOT_CONFIG
from config.position_modes import resolve_grid_engine_config

# Celý modul je pomalý (2letý backtest ~4–5 min) — gate ho pouští jen bez -m "not slow".
pytestmark = pytest.mark.slow


@functools.lru_cache(maxsize=1)
def _window() -> tuple[str | None, str | None]:
    """Centralni 2-lete okno odvozene z datasetu (BACKTEST_WINDOW_YEARS)."""
    df_full = load_csv(csv_path_for(LIVE_BOT_CONFIG.symbol, LIVE_BOT_CONFIG.timeframe_label))
    return default_backtest_date_range(df_full)


# Golden baseline — WAVE slice (compute_stats, track_concurrent=True)
EXPECTED_TRADES_WAVE = 751
EXPECTED_NET_PNL_WAVE_USD = 279156.28

# Fingerprint vsech 1094 closed_trades (serazeno close_time, entry_time, wave_time).
# Naměřeno na 2letém okně (re-baseline); run() i process_bar daly identicky otisk.
EXPECTED_CLOSED_TRADES_COUNT = 1094
EXPECTED_CLOSED_TRADES_SHA256 = (
    "ab3f64677da0b33f7a2d26d0a856621a866c9d3f0d3a8f237a9bf96188f95e7b"
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
    date_from, date_to = _window()
    cfg = resolve_grid_engine_config(
        LIVE_BOT_CONFIG,
        date_from=date_from,
        date_to=date_to,
    )
    assert getattr(cfg, "wave_detection_mode", "legacy_precompute") == "legacy_precompute"

    df = load_data(
        cfg.symbol,
        cfg.timeframe_label,
        date_from,
        date_to,
    )
    assert not df.empty

    engine = BacktestEngine(cfg)
    trades = engine.run(df)
    return engine, trades


def _run_prepare_process_bar_loop(df) -> tuple[BacktestEngine, List[ClosedTrade]]:
    """Replikuje telo run() po prepare — overuje extrakci process_bar z 1D."""
    date_from, date_to = _window()
    cfg = resolve_grid_engine_config(
        LIVE_BOT_CONFIG,
        date_from=date_from,
        date_to=date_to,
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
    date_from, date_to = _window()
    cfg = resolve_grid_engine_config(
        LIVE_BOT_CONFIG,
        date_from=date_from,
        date_to=date_to,
    )
    df = load_data(cfg.symbol, cfg.timeframe_label, date_from, date_to)

    trades_run = BacktestEngine(cfg).run(df.copy())
    _, trades_manual = _run_prepare_process_bar_loop(df.copy())

    assert _closed_trade_rows(trades_run) == _closed_trade_rows(trades_manual)
