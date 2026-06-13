"""Task 6: PP jen ve smeru close-based BOS trendu (ne seed-reset)."""
from __future__ import annotations

from unittest.mock import patch

import pandas as pd

from config.bot_config import BotConfig
from runtime.live_loop import _maybe_fire_pp_break_event
from strategy.trend_bos import pp_trend_confirmed_by_close_bos


def _cfg() -> BotConfig:
    return BotConfig(
        wave_min_pct=0.26,
        min_opp_bars=3,
        pp_enabled=True,
        trend_filter_enabled=True,
    )


def test_pp_skipped_logs_when_trend_not_close_confirmed():
    cfg = _cfg()
    df = pd.DataFrame(
        {
            "time": pd.date_range("2026-03-01", periods=5, freq="30min"),
            "open": [1.0, 1.0, 1.0, 1.0, 1.0],
            "high": [1.01, 1.01, 1.01, 1.01, 1.01],
            "low": [0.99, 0.99, 0.99, 0.99, 0.99],
            "close": [1.0, 1.0, 1.0, 1.0, 1.0],
        }
    )
    waves: list = []
    with patch(
        "runtime.live_loop.pp_trend_confirmed_by_close_bos",
        return_value=False,
    ):
        with patch("runtime.live_loop.log_event") as log_event:
            _maybe_fire_pp_break_event(
                cfg=cfg,
                df=df,
                waves=waves,
                current_trend="bull",
                processed_pp_wave_times=set(),
                wave_birth_by_time={},
            )
    log_event.assert_called_once()
    assert log_event.call_args[0][2] == "PP_SKIPPED_TREND_FROM_SEED_RESET"


def test_pp_trend_confirmed_rejects_neutral():
    cfg = _cfg()
    df = pd.DataFrame(
        {
            "time": pd.date_range("2026-03-01", periods=3, freq="30min"),
            "close": [1.0, 1.0, 1.0],
            "open": [1.0, 1.0, 1.0],
            "high": [1.0, 1.0, 1.0],
            "low": [1.0, 1.0, 1.0],
        }
    )
    assert pp_trend_confirmed_by_close_bos(df, [], cfg, "neutral") is False
