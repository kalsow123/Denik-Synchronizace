"""2D — load_bars(mt5) forming-bar strip (mock get_bars, bez terminalu)."""
from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest

from config.bot_config import LIVE_BOT_CONFIG
from core.market_data import OHLC_COLUMNS, load_bars


def _fake_mt5_df(n: int) -> pd.DataFrame:
    """Simuluj get_bars: n barů včetně forming na posledním řádku."""
    times = pd.date_range("2026-01-01", periods=n, freq="30min")
    return pd.DataFrame(
        {
            "time": times,
            "open": [1.0 + i * 0.0001 for i in range(n)],
            "high": [1.1] * n,
            "low": [0.9] * n,
            "close": [1.05] * n,
            "tick_volume": [100] * n,
        }
    )


@patch("infra.market_data.get_bars")
def test_load_bars_mt5_strips_forming_by_default(mock_get_bars):
    n = 10
    mock_get_bars.return_value = _fake_mt5_df(n)

    closed = load_bars(LIVE_BOT_CONFIG, source="mt5", n=n, include_forming=False)

    assert closed is not None
    assert len(closed) == n - 1
    assert list(closed.columns) == list(OHLC_COLUMNS)
    assert pd.Timestamp(closed["time"].iloc[-1]) == pd.Timestamp("2026-01-01 04:00")
    mock_get_bars.assert_called_once_with(LIVE_BOT_CONFIG, n)


@patch("infra.market_data.get_bars")
def test_load_bars_mt5_include_forming_keeps_last_row(mock_get_bars):
    n = 10
    raw = _fake_mt5_df(n)
    mock_get_bars.return_value = raw

    full = load_bars(LIVE_BOT_CONFIG, source="mt5", n=n, include_forming=True)

    assert full is not None
    assert len(full) == n
    assert pd.Timestamp(full["time"].iloc[-1]) == pd.Timestamp("2026-01-01 04:30")


@patch("infra.market_data.get_bars")
def test_load_bars_mt5_empty_returns_none(mock_get_bars):
    mock_get_bars.return_value = None
    assert load_bars(LIVE_BOT_CONFIG, source="mt5", n=5) is None
