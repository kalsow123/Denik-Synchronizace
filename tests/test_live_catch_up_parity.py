"""2B — catch-up parity: N missed barů po jednom == batch (bez missed_bar_replay).

VARIANTA A.txt §5.2, test 2. Catch-up = N× process_bar nad sdíleným ctx, takže
zpracování 3 zmeškaných barů po jednom dá IDENTICKÝ výsledek jako dávka. Náhradní
cesta (`LiveEngineSession.catch_up_missed`) NEimportuje `runtime.missed_bar_replay`.
"""
from __future__ import annotations

import inspect
from dataclasses import replace
from typing import List, Tuple

import pandas as pd
import pytest

from backtest.executor import BacktestExecutor
from backtest.grid.data_cache import clear_cache, load_data
from backtest.wave_sim_cache import clear_pine_sim_cache
from config.bot_config import LIVE_BOT_CONFIG
from config.enums import WaveDetectionMode
from config.position_modes import resolve_grid_engine_config
import runtime.live_engine_session as live_engine_session_mod
from runtime.live_engine_session import LiveEngineSession

DATE_FROM = "2025-11-10"
DATE_TO = "2026-02-09"


def _decision_rows(trades) -> List[Tuple[str, str, str, str, float]]:
    rows = []
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
    return replace(cfg, wave_detection_mode=WaveDetectionMode.INCREMENTAL_CAUSAL)


def _new_session(cfg, df) -> LiveEngineSession:
    session = LiveEngineSession(cfg, df.copy())
    session.executor = BacktestExecutor(session.engine)
    session.engine._executor = session.executor
    return session


def _finalized_trades(session: LiveEngineSession) -> list:
    last_ix = len(session._df) - 1
    session.engine._close_remaining(last_ix, session._df)
    return list(session.engine.closed_trades)


@pytest.fixture(autouse=True)
def _reset_caches():
    clear_cache()
    clear_pine_sim_cache()
    yield
    clear_cache()
    clear_pine_sim_cache()


def test_catch_up_one_by_one_equals_batch():
    cfg = _incremental_cfg()
    df = load_data(cfg.symbol, cfg.timeframe_label, DATE_FROM, DATE_TO)
    assert not df.empty
    n = len(df)
    assert n > 6

    # Batch: všechny closed bary v jednom process_closed_bars volání.
    session_batch = _new_session(cfg, df)
    session_batch.process_closed_bars(df, list(range(1, n)))
    trades_batch = _finalized_trades(session_batch)

    # Split: warm-up 1..n-4, pak 3 "missed" bary přes catch_up_missed po jednom.
    session_split = _new_session(cfg, df)
    session_split.process_closed_bars(df, list(range(1, n - 3)))
    last_ts = pd.Timestamp(df["time"].iloc[n - 4])
    missed = session_split.catch_up_missed(df, last_ts)
    assert missed == [n - 3, n - 2, n - 1]
    for idx in missed:  # po jednom
        session_split.process_closed_bars(df, [idx])
    trades_split = _finalized_trades(session_split)

    assert _decision_rows(trades_batch) == _decision_rows(trades_split)


def test_catch_up_does_not_import_missed_bar_replay():
    """Náhradní catch-up cesta nesmí IMPORTOVAT missed_bar_replay (smaže se v 2G)."""
    src = inspect.getsource(live_engine_session_mod)
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith(("import ", "from ")):
            assert "missed_bar_replay" not in stripped, (
                f"live_engine_session nesmí importovat missed_bar_replay: {stripped!r}"
            )
