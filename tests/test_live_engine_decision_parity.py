"""2B — decision parity: live cesta (LiveEngineSession) == backtester rozhodnutí.

VARIANTA A.txt §5.2, test 1. Parita se ověřuje NA BACKTESTERU (NE E2E, viz §4.4).

  Běh A = `BacktestEngine.run(df)` s BacktestExecutor (incremental_causal).
  Běh B = LiveEngineSession-style loop `process_bar(1..n)` s RecordingExecutorem
          (spy wrapping BacktestExecutor) + backtest-only finalizace (_close_remaining).

Assert: stejné closed_trades (wave_time, close_reason, pnl) → jeden rozhodovač.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import replace
from typing import List, Sequence, Tuple

import pytest

from backtest.engine import BacktestEngine
from backtest.executor import BacktestExecutor, Executor
from backtest.grid.data_cache import clear_cache, load_data
from backtest.wave_sim_cache import clear_pine_sim_cache
from config.bot_config import LIVE_BOT_CONFIG
from config.enums import WaveDetectionMode
from config.position_modes import resolve_grid_engine_config
from runtime.live_engine_session import LiveEngineSession

DATE_FROM = "2025-11-10"
DATE_TO = "2026-05-09"


class RecordingExecutor(Executor):
    """Spy: deleguje na BacktestExecutor a počítá volání (důkaz pass-through)."""

    def __init__(self, inner: BacktestExecutor) -> None:
        self._inner = inner
        self.calls: Counter[str] = Counter()

    def place_pending(self, order, bar_idx, bar_time):
        self.calls["place_pending"] += 1
        return self._inner.place_pending(order, bar_idx, bar_time)

    def place_market(self, trade, bar_idx, bar_time):
        self.calls["place_market"] += 1
        return self._inner.place_market(trade, bar_idx, bar_time)

    def close_position(self, trade, *, reason, price, bar_idx, bar_time):
        self.calls["close_position"] += 1
        return self._inner.close_position(
            trade, reason=reason, price=price, bar_idx=bar_idx, bar_time=bar_time
        )

    def cancel_pending(self, order):
        self.calls["cancel_pending"] += 1
        return self._inner.cancel_pending(order)

    def modify_sltp(self, trade, *, sl=None, tp=None):
        self.calls["modify_sltp"] += 1
        return self._inner.modify_sltp(trade, sl=sl, tp=tp)

    def close_partial(self, trade, lot, *, reason, price, bar_idx, bar_time):
        self.calls["close_partial"] += 1
        return self._inner.close_partial(
            trade, lot, reason=reason, price=price, bar_idx=bar_idx, bar_time=bar_time
        )

    def modify_lot(self, trade, lot):
        self.calls["modify_lot"] += 1
        return self._inner.modify_lot(trade, lot)

    def get_open_positions(self):
        return self._inner.get_open_positions()

    def get_pendings(self):
        return self._inner.get_pendings()

    def on_bar_open(self, bar_idx, bar_time, high, low, open_):
        self.calls["on_bar_open"] += 1
        return self._inner.on_bar_open(bar_idx, bar_time, high, low, open_)

    def on_bar_range(self, bar_idx, bar_time, high, low):
        self.calls["on_bar_range"] += 1
        return self._inner.on_bar_range(bar_idx, bar_time, high, low)

    def prune_pendings(self, mid_price):
        return self._inner.prune_pendings(mid_price)

    def enforce_overflow(self, bar_idx, bar_time, mid_price):
        return self._inner.enforce_overflow(bar_idx, bar_time, mid_price)

    def expire_pendings(self, bar_idx, bar_time):
        return self._inner.expire_pendings(bar_idx, bar_time)


ClosedRow = Tuple[str, str, str, float]


def _decision_rows(trades) -> List[ClosedRow]:
    """(entry_time, close_time, wave_time, close_reason, pnl) — rozhodovací otisk."""
    rows: List[Tuple[str, str, str, str, float]] = []
    for t in sorted(trades, key=lambda x: (x.close_time, x.entry_time, x.wave_time)):
        rows.append(
            (
                str(t.entry_time),
                str(t.close_time),
                str(t.wave_time),
                str(t.close_reason),
                round(float(t.pnl_usd), 2),
            )
        )
    return rows


def _incremental_cfg():
    cfg = resolve_grid_engine_config(LIVE_BOT_CONFIG, date_from=DATE_FROM, date_to=DATE_TO)
    cfg = replace(cfg, wave_detection_mode=WaveDetectionMode.INCREMENTAL_CAUSAL)
    assert cfg.causal_mode is True
    return cfg


@pytest.fixture(autouse=True)
def _reset_caches():
    clear_cache()
    clear_pine_sim_cache()
    yield
    clear_cache()
    clear_pine_sim_cache()


def test_live_session_loop_matches_backtester_closed_trades():
    cfg = _incremental_cfg()
    df = load_data(cfg.symbol, cfg.timeframe_label, DATE_FROM, DATE_TO)
    assert not df.empty

    # Běh A — backtester (engine.run = prepare + process_bar loop + finalize).
    trades_a = BacktestEngine(cfg).run(df.copy())

    # Běh B — live strangler: LiveEngineSession (incremental) + RecordingExecutor.
    session = LiveEngineSession(cfg, df.copy())
    recorder = RecordingExecutor(BacktestExecutor(session.engine))
    session.executor = recorder
    session.engine._executor = recorder

    indices = session.catch_up_missed(session._df, None)  # 0..n-1 (0 se přeskočí)
    session.process_closed_bars(session._df, indices)
    # Backtest-only finalizace (live pozice nezavírá) — pro paritní porovnání.
    last_ix = len(session._df) - 1
    session.engine._close_remaining(last_ix, session._df)
    trades_b = list(session.engine.closed_trades)

    assert _decision_rows(trades_a) == _decision_rows(trades_b)
    # Pass-through důkaz: rozhodnutí prošla executorem (ne bypass).
    assert recorder.calls["place_pending"] > 0
    assert recorder.calls["on_bar_open"] > 0
    assert recorder.calls["on_bar_range"] > 0
