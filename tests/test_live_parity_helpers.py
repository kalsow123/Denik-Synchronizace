"""Testy paritních helperů live vs backtest."""
from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from infra.orders import decision_prices_from_bar_close
from runtime.live_engine_session import new_closed_bar_indices


def test_decision_prices_from_bar_close_uses_half_spread():
    tick = SimpleNamespace(ask=1.1010, bid=1.0990)
    ask, bid = decision_prices_from_bar_close(1.1000, tick)
    assert abs(ask - 1.1010) < 1e-9
    assert abs(bid - 1.0990) < 1e-9


def test_decision_prices_from_bar_close_none_uses_tick():
    tick = SimpleNamespace(ask=1.1015, bid=1.0995)
    ask, bid = decision_prices_from_bar_close(None, tick)
    assert ask == 1.1015
    assert bid == 1.0995


def test_new_closed_bar_indices_after_timestamp():
    df = pd.DataFrame(
        {
            "time": pd.to_datetime(
                ["2026-01-01 10:00", "2026-01-01 10:30", "2026-01-01 11:00"],
            ),
            "close": [1.0, 1.1, 1.2],
        },
    )
    last = pd.Timestamp("2026-01-01 10:00")
    assert new_closed_bar_indices(df, last) == [1, 2]


def test_new_closed_bar_indices_first_run():
    df = pd.DataFrame(
        {
            "time": pd.to_datetime(["2026-01-01 10:00", "2026-01-01 10:30"]),
            "close": [1.0, 1.1],
        },
    )
    assert new_closed_bar_indices(df, None) == [0, 1]
