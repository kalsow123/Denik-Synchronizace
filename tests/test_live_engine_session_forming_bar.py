"""2B — live-only kontrakt: forming bar se zahodí PŘED enginem (NE process_bar).

VARIANTA A.txt §5.2 test 3 / §4.4. MT5 `get_bars()` vrací i nedokončený (forming)
bar; live MUSÍ běžet jen na uzavřených barech. Tady mockujeme get_bars (vrátí df
včetně forming baru) a ověříme, že `LiveEngineSession.closed_bars_only` forming bar
zahodí a `process_bar` na forming indexu/čase NIKDY neběží.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pandas as pd
import pytest

from backtest.executor import BacktestExecutor, Executor
from backtest.grid.data_cache import clear_cache, load_data
from backtest.wave_sim_cache import clear_pine_sim_cache
from config.bot_config import LIVE_BOT_CONFIG
from config.enums import WaveDetectionMode
from config.position_modes import resolve_grid_engine_config
from runtime.live_engine_session import LiveEngineSession

DATE_FROM = "2025-11-10"
DATE_TO = "2026-01-10"


class _BarIdxRecorder(Executor):
    """Spy: zaznamená bar_idx každého process_bar (on_bar_open) + deleguje."""

    def __init__(self, inner: BacktestExecutor) -> None:
        self._inner = inner
        self.processed_bar_idx: list[int] = []

    def place_pending(self, order, bar_idx, bar_time):
        return self._inner.place_pending(order, bar_idx, bar_time)

    def place_market(self, trade, bar_idx, bar_time):
        return self._inner.place_market(trade, bar_idx, bar_time)

    def close_position(self, trade, *, reason, price, bar_idx, bar_time):
        return self._inner.close_position(
            trade, reason=reason, price=price, bar_idx=bar_idx, bar_time=bar_time
        )

    def cancel_pending(self, order):
        return self._inner.cancel_pending(order)

    def modify_sltp(self, trade, *, sl=None, tp=None):
        return self._inner.modify_sltp(trade, sl=sl, tp=tp)

    def close_partial(self, trade, lot, *, reason, price, bar_idx, bar_time):
        return self._inner.close_partial(
            trade, lot, reason=reason, price=price, bar_idx=bar_idx, bar_time=bar_time
        )

    def modify_lot(self, trade, lot):
        return self._inner.modify_lot(trade, lot)

    def get_open_positions(self):
        return self._inner.get_open_positions()

    def get_pendings(self):
        return self._inner.get_pendings()

    def on_bar_open(self, bar_idx, bar_time, high, low, open_):
        self.processed_bar_idx.append(int(bar_idx))
        return self._inner.on_bar_open(bar_idx, bar_time, high, low, open_)

    def on_bar_range(self, bar_idx, bar_time, high, low):
        return self._inner.on_bar_range(bar_idx, bar_time, high, low)

    def prune_pendings(self, mid_price):
        return self._inner.prune_pendings(mid_price)

    def enforce_overflow(self, bar_idx, bar_time, mid_price):
        return self._inner.enforce_overflow(bar_idx, bar_time, mid_price)

    def expire_pendings(self, bar_idx, bar_time):
        return self._inner.expire_pendings(bar_idx, bar_time)


def _incremental_cfg():
    cfg = resolve_grid_engine_config(LIVE_BOT_CONFIG, date_from=DATE_FROM, date_to=DATE_TO)
    return replace(cfg, wave_detection_mode=WaveDetectionMode.INCREMENTAL_CAUSAL)


def _df_with_forming_bar(df_closed: pd.DataFrame) -> pd.DataFrame:
    """Simuluj MT5 get_bars: připoj nedokončený (forming) bar jako poslední řádek."""
    last = df_closed.iloc[-1]
    last_t = pd.Timestamp(last["time"])
    forming = {
        "time": last_t + timedelta(minutes=30),
        "open": float(last["close"]),
        "high": float(last["close"]) + 0.0010,
        "low": float(last["close"]) - 0.0010,
        "close": float(last["close"]) + 0.0005,
    }
    cols = list(df_closed.columns)
    forming_row = {c: forming.get(c, last[c]) for c in cols}
    return pd.concat(
        [df_closed, pd.DataFrame([forming_row])], ignore_index=True
    )


@pytest.fixture(autouse=True)
def _reset_caches():
    clear_cache()
    clear_pine_sim_cache()
    yield
    clear_cache()
    clear_pine_sim_cache()


def test_forming_bar_stripped_before_engine_and_process_bar():
    cfg = _incremental_cfg()
    df_closed = load_data(cfg.symbol, cfg.timeframe_label, DATE_FROM, DATE_TO)
    assert not df_closed.empty

    # Mock get_bars → vrací df VČETNĚ forming baru (poslední řádek).
    def fake_get_bars():
        return _df_with_forming_bar(df_closed)

    raw = fake_get_bars()
    forming_time = pd.Timestamp(raw["time"].iloc[-1])
    assert len(raw) == len(df_closed) + 1

    # Live-only kontrakt: forming bar se zahodí PŘED enginem.
    closed = LiveEngineSession.closed_bars_only(raw)
    assert len(closed) == len(raw) - 1
    assert pd.Timestamp(closed["time"].iloc[-1]) == pd.Timestamp(df_closed["time"].iloc[-1])
    assert forming_time not in set(pd.Timestamp(t) for t in closed["time"])

    # Engine + process_bar běží JEN na uzavřených barech.
    session = LiveEngineSession(cfg, closed)
    recorder = _BarIdxRecorder(BacktestExecutor(session.engine))
    session.executor = recorder
    session.engine._executor = recorder

    indices = session.catch_up_missed(closed, None)
    session.process_closed_bars(closed, indices)

    assert recorder.processed_bar_idx, "process_bar musí proběhnout na uzavřených barech"
    forming_idx = len(raw) - 1
    assert forming_idx not in recorder.processed_bar_idx
    # Nejvyšší zpracovaný index = poslední UZAVŘENÝ bar (forming nikdy).
    assert max(recorder.processed_bar_idx) == len(closed) - 1
    # Žádný zpracovaný bar nemá čas forming baru.
    processed_times = {pd.Timestamp(closed["time"].iloc[i]) for i in recorder.processed_bar_idx}
    assert forming_time not in processed_times
