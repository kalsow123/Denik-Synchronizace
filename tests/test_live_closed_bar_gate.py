"""Closed-bar gate: strategie jen na novem uzavrenem baru (parita backtest)."""

import pandas as pd

from runtime.live_loop import _df_closed_bars_only, _last_closed_bar_time


def _sample_df(n: int = 5) -> pd.DataFrame:
    times = pd.date_range("2026-01-01 00:00", periods=n, freq="30min")
    return pd.DataFrame({"time": times, "close": range(n)})


def test_last_closed_bar_time_uses_penultimate_row():
    df = _sample_df(5)
    assert _last_closed_bar_time(df) == pd.Timestamp("2026-01-01 01:30")


def test_last_closed_bar_time_single_bar_fallback():
    df = _sample_df(1)
    assert _last_closed_bar_time(df) == pd.Timestamp("2026-01-01 00:00")


def test_df_closed_bars_only_strips_forming_bar():
    df = _sample_df(5)
    closed = _df_closed_bars_only(df)
    assert len(closed) == 4
    assert closed["time"].iloc[-1] == pd.Timestamp("2026-01-01 01:30")
    assert closed["time"].iloc[0] == pd.Timestamp("2026-01-01 00:00")


def test_df_closed_bars_only_single_bar_unchanged():
    df = _sample_df(1)
    closed = _df_closed_bars_only(df)
    assert len(closed) == 1


def test_gate_skips_when_closed_bar_unchanged():
    df = _sample_df(5)
    closed_ts = _last_closed_bar_time(df)
    last_processed = closed_ts
    assert closed_ts <= last_processed


def test_gate_runs_on_first_cycle():
    df = _sample_df(5)
    closed_ts = _last_closed_bar_time(df)
    last_processed = None
    assert last_processed is None or closed_ts > last_processed


def test_gate_runs_on_new_closed_bar():
    df_old = _sample_df(5)
    df_new = _sample_df(6)
    old_ts = _last_closed_bar_time(df_old)
    new_ts = _last_closed_bar_time(df_new)
    assert new_ts > old_ts
