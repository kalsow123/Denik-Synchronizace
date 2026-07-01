"""3D — persistent session parity: refresh_df_if_needed() + full-window replay
po každém novém baru (JEDNA session instance) == jednorázový dávkový (batch) běh.

VARIANTA A.txt / FÁZE 3, akce 3D (oprava P3/P4). `_run_live_loop_backtester`
vytváří `LiveEngineSession` JEDNOU při startu loopu; při každém novém closed baru
se dál jen `refresh_df_if_needed()` (prepare() znovu POUZE při skutečně novém
baru) a přehraje CELÉ aktuální okno (`process_closed_bars(df, range(1, len(df)))`)
— NE nová session/engine instance a NE jen poslední bar (to byl P3 bug: engine
`prepare()` je destruktivní — resetuje `open_trades`/`pending_orders`/
`sent_signals`/`wave_birth_by_time`; nová session nebo zpracování jen jednoho
nového baru na čerstvě resetnutém enginu = ztráta nahromaděného stavu mezi bary).

Tento test simuluje přesně tuto orchestraci (rostoucí okno bar-po-baru na JEDNÉ
`LiveEngineSession` instanci) a porovnává výsledné `closed_trades` s jednorázovým
dávkovým zpracováním celého df — musí být identické (deterministický replay).

Vzor/helpery (`_decision_rows`, `_new_session`, `_finalized_trades`, cache fixture)
zkopírovány a upraveny z `tests/test_live_catch_up_parity.py`. Stejné cfg/df —
3měsíční slice je OK pro rychlost (tento test ověřuje shodu chování persistentní
vs. batch session, ne backtest metriky/baseline čísla — POVINNÉ 2leté okno platí
pro baseline/backtest validaci, ne pro tento parity test).
"""
from __future__ import annotations

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


def test_persistent_session_growing_window_equals_batch():
    """JEDNA session, rostouci okno bar-po-baru (refresh_df_if_needed + full-window
    replay) == batch process_closed_bars(range(1, n)) na cele df najednou."""
    cfg = _incremental_cfg()
    df = load_data(cfg.symbol, cfg.timeframe_label, DATE_FROM, DATE_TO)
    assert not df.empty
    n = len(df)
    assert n > 20

    # Batch = referencni "spravny" vysledek (cele df v jednom process_closed_bars).
    session_batch = _new_session(cfg, df)
    session_batch.process_closed_bars(df, list(range(1, n)))
    trades_batch = _finalized_trades(session_batch)

    # Persistent = presne orchestrace z _run_live_loop_backtester po oprave P3/P4:
    #   1) cold start (LiveEngineSession.__init__ = prepare()) + prvni plny replay
    #      pocatecniho okna,
    #   2) pro dalsi bary: refresh_df_if_needed() (prepare() znovu jen kdyz
    #      pribyl novy bar) + plny replay CELEHO aktualniho okna,
    #   3) po celou dobu JEDNA session/engine instance (zadne nove vytvareni).
    #
    # Pozn. rychlosti: kazdy krok = O(len(window)) prepare+replay, takze simulujeme
    # jen NEKOLIK rustovych kroku (ne bar-po-baru pres celych ~n/2 baru, coz by bylo
    # O(n^2) a v testu zbytecne pomale) — pro overeni orchestrace (refresh + replay +
    # stejna instance) staci male mnozstvi reprezentativnich kroku.
    warm_up_n = max(10, int(n * 0.9))
    step = max(1, (n - warm_up_n) // 4)
    session_live = _new_session(cfg, df.iloc[:warm_up_n].reset_index(drop=True))
    engine_id_before = id(session_live.engine)
    session_live.process_closed_bars(session_live._df, list(range(1, warm_up_n)))

    growth_points = sorted(set(range(warm_up_n + step, n, step)) | {n})
    for k in growth_points:
        window = df.iloc[:k].reset_index(drop=True)
        refreshed = session_live.refresh_df_if_needed(window)
        assert refreshed, f"refresh_df_if_needed melo detekovat novy bar na k={k}"
        session_live.process_closed_bars(window, list(range(1, len(window))))

    assert id(session_live.engine) == engine_id_before, (
        "LiveEngineSession musi zustat JEDNA instance behem cele zivotnosti loopu "
        "(P3 fix — zadna nova session/engine pri kazdem novem baru)."
    )

    trades_live = _finalized_trades(session_live)
    assert _decision_rows(trades_batch) == _decision_rows(trades_live)


def test_refresh_df_if_needed_is_noop_without_new_bar():
    """5s polling dedup: opakovane volani se STEJNYM oknem nesmi znovu volat prepare()
    (ctx zustava STEJNY objekt — zadny zbytecny reset stavu mezi pollingy)."""
    cfg = _incremental_cfg()
    df = load_data(cfg.symbol, cfg.timeframe_label, DATE_FROM, DATE_TO)
    assert not df.empty

    session = _new_session(cfg, df)
    ctx_before = session.ctx

    refreshed = session.refresh_df_if_needed(df.copy())

    assert refreshed is False
    assert session.ctx is ctx_before


def test_refresh_df_if_needed_prepares_on_new_bar():
    """Nový closed bar (delší df / jiný poslední timestamp) MUSÍ vyvolat prepare()
    (jinak by session zůstala na starém oknu a nikdy by neviděla nové bary)."""
    cfg = _incremental_cfg()
    df = load_data(cfg.symbol, cfg.timeframe_label, DATE_FROM, DATE_TO)
    assert not df.empty
    n = len(df)
    assert n > 5

    session = _new_session(cfg, df.iloc[: n - 1].reset_index(drop=True))
    ctx_before = session.ctx

    refreshed = session.refresh_df_if_needed(df)

    assert refreshed is True
    assert session.ctx is not ctx_before
    assert len(session._df) == n
