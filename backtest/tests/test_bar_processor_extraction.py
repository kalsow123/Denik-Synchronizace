"""2A — testy k extrakci `BarProcessor` (core/bar_processor.py).

Overuje, ze presun tela `process_bar` z `BacktestEngine` do samostatneho
`BarProcessor` je CISTY REFACTOR bez zmeny chovani:
  - `engine.run()` (ktery teď deleguje na BarProcessor) dava bit-identicke
    closed_trades jako golden baseline (164 / +39040.88, fingerprint 226).
  - cesta primo pres `BarProcessor.process_bar(...)` (stejny df, stejny
    BacktestExecutor) dava identickou sekvenci closed_trades jako `engine.run()`.
  - `BarProcessor` drzi referenci na engine (stav sdileny, ne kopirovany).
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
from core.bar_processor import BarProcessor
from config.bot_config import LIVE_BOT_CONFIG
from config.position_modes import resolve_grid_engine_config

DATE_FROM = "2025-11-10"
DATE_TO = "2026-05-09"

EXPECTED_TRADES_WAVE = 164
EXPECTED_NET_PNL_WAVE_USD = 39040.88
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


def _make_cfg():
    return resolve_grid_engine_config(
        LIVE_BOT_CONFIG,
        date_from=DATE_FROM,
        date_to=DATE_TO,
    )


def _run_via_bar_processor(df) -> List[ClosedTrade]:
    """Telo run() po prepare, ale process_bar volame PRIMO pres BarProcessor."""
    cfg = _make_cfg()
    engine = BacktestEngine(cfg)
    ctx = engine.prepare(df)
    executor = BacktestExecutor(engine)
    engine._executor = executor

    processor = BarProcessor(engine)
    for i in range(1, ctx.ohlc.n):
        processor.process_bar(i, ctx, executor)

    last_ix = len(df) - 1
    engine._close_remaining(last_ix, df)
    return list(engine.closed_trades)


@pytest.fixture(autouse=True)
def _reset_caches():
    clear_cache()
    clear_pine_sim_cache()
    yield
    clear_cache()
    clear_pine_sim_cache()


def test_engine_holds_bar_processor_by_reference():
    """BacktestEngine vytvori BarProcessor drzici referenci na sebe (sdileny stav)."""
    engine = BacktestEngine(_make_cfg())
    assert isinstance(engine._bar_processor, BarProcessor)
    assert engine._bar_processor.engine is engine


def test_engine_run_matches_golden_after_extraction():
    """run() (delegujici na BarProcessor) zustava bit-identicky s golden baseline."""
    cfg = _make_cfg()
    df = load_data(cfg.symbol, cfg.timeframe_label, DATE_FROM, DATE_TO)
    assert not df.empty

    trades = BacktestEngine(cfg).run(df)
    stats = compute_stats(trades_to_df(trades), track_concurrent=True)

    assert stats["trades_wave"] == EXPECTED_TRADES_WAVE
    assert stats["net_pnl_wave_usd"] == EXPECTED_NET_PNL_WAVE_USD

    digest, count = _closed_trades_fingerprint(trades)
    assert count == EXPECTED_CLOSED_TRADES_COUNT
    assert digest == EXPECTED_CLOSED_TRADES_SHA256


def test_bar_processor_path_equals_engine_run():
    """Primy beh pres BarProcessor.process_bar == engine.run() (stejne closed_trades)."""
    cfg = _make_cfg()
    df = load_data(cfg.symbol, cfg.timeframe_label, DATE_FROM, DATE_TO)

    trades_run = BacktestEngine(cfg).run(df.copy())
    trades_processor = _run_via_bar_processor(df.copy())

    assert _closed_trade_rows(trades_run) == _closed_trade_rows(trades_processor)
