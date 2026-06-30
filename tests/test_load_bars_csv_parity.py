"""2D — load_bars(csv) vraci stejny DataFrame jako load_csv (parita schema + hodnot)."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from backtest.data_loader import load_csv
from backtest.grid.data_cache import csv_path_for
from config.bot_config import LIVE_BOT_CONFIG
from core.market_data import OHLC_COLUMNS, load_bars

EURUSD_M30 = csv_path_for("EURUSD", "M30")


@pytest.mark.skipif(not EURUSD_M30.exists(), reason=f"Chybi {EURUSD_M30}")
def test_load_bars_csv_matches_load_csv_full_file():
    direct = load_csv(EURUSD_M30)
    direct = direct.loc[:, list(OHLC_COLUMNS)].reset_index(drop=True)

    via = load_bars(LIVE_BOT_CONFIG, source="csv", path=EURUSD_M30)
    assert via is not None
    assert list(via.columns) == list(OHLC_COLUMNS)
    pd.testing.assert_frame_equal(via, direct)


@pytest.mark.skipif(not EURUSD_M30.exists(), reason=f"Chybi {EURUSD_M30}")
def test_load_bars_csv_default_path_from_cfg():
    via_default = load_bars(LIVE_BOT_CONFIG, source="csv")
    via_explicit = load_bars(LIVE_BOT_CONFIG, source="csv", path=EURUSD_M30)
    pd.testing.assert_frame_equal(via_default, via_explicit)
