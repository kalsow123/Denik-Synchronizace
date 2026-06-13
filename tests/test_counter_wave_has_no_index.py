"""Test pro T2: Counter vlny a HH/HL fail vlny nemaji index_in_trend."""
from __future__ import annotations

import pandas as pd

from config.bot_config import BotConfig
from strategy.wave_sequence import compute_wave_sequence_info_per_wave


def _cfg() -> BotConfig:
    return BotConfig(
        wave_min_pct=0.26,
        min_opp_bars=3,
        trend_filter_enabled=True,
        trend_hh_hl_filter_enabled=True,
    )


def test_counter_wave_has_no_index():
    df = pd.DataFrame(
        {
            "time": pd.date_range("2026-03-01 00:00", periods=50, freq="30min"),
            "open": 1.10,
            "high": 1.12,
            "low": 1.08,
            "close": 1.11,
        }
    )

    # Vlny v bull trendu (nastavi bull hned prvni vlnou)
    up1 = {
        "wave_time": "up1",
        "dir": 1,
        "draw_right": 5,
        "box_top": 1.10,
        "box_bottom": 1.05,
    }
    up2 = {
        "wave_time": "up2",
        "dir": 1,
        "draw_right": 10,
        "box_top": 1.15,
        "box_bottom": 1.08,  # HH, HL
    }
    dn_counter = {
        "wave_time": "dn_counter",
        "dir": -1,
        "draw_right": 15,
        "box_top": 1.14,
        "box_bottom": 1.09,
    }
    up3 = {
        "wave_time": "up3",
        "dir": 1,
        "draw_right": 20,
        "box_top": 1.20,
        "box_bottom": 1.10,  # HH, HL vuci up2
    }

    waves = [up1, up2, dn_counter, up3]

    seq = compute_wave_sequence_info_per_wave(df, waves, _cfg())

    assert seq["up1"].index_in_trend == 1
    assert seq["up2"].index_in_trend == 2
    assert seq["dn_counter"].index_in_trend is None
    assert seq["up3"].index_in_trend == 3
