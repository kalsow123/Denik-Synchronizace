"""EXT counter cas — blokace po dalsi vlne + jednorazove otevreni."""
from __future__ import annotations

import pandas as pd

from backtest.engine import BacktestEngine
from backtest.grid.translator import grid_dict_to_bot_config
from strategy.ext_logic import ext_counter_time_may_open


def test_may_open_when_no_subsequent_wave():
    assert ext_counter_time_may_open(
        bos_state="armed",
        suppressed_after_subsequent_wave=False,
        counter_time_already_done=False,
        counter_bos_already_done=False,
    )


def test_blocked_after_first_post_ext_wave_confirmed():
    assert not ext_counter_time_may_open(
        bos_state="armed",
        suppressed_after_subsequent_wave=True,
        counter_time_already_done=False,
        counter_bos_already_done=False,
    )


def test_blocked_when_time_counter_already_done():
    assert not ext_counter_time_may_open(
        bos_state="armed",
        suppressed_after_subsequent_wave=False,
        counter_time_already_done=True,
        counter_bos_already_done=False,
    )


def test_blocked_when_bos_counter_already_done():
    assert not ext_counter_time_may_open(
        bos_state="armed",
        suppressed_after_subsequent_wave=False,
        counter_time_already_done=False,
        counter_bos_already_done=True,
    )


def test_blocked_when_shared_ext_bos_cancel_state_is_cancelled():
    assert not ext_counter_time_may_open(
        bos_state="cancelled",
        suppressed_after_subsequent_wave=False,
        counter_time_already_done=False,
        counter_bos_already_done=False,
    )


def test_engine_ext_counter_at_21h_while_ext_forming_before_confirm():
    """
    EXT dosahne ext_wave_min_pct jeste pred min_opp_bars (forming bar < birth bar).
    Pokud prvni vlna po EXT nedosahne ext_wave_min_pct pred 21:00, counter se otevre.
    """
    cfg = grid_dict_to_bot_config(
        {
            "timeframe": "M30",
            "wave_min_pct": 0.26,
            "min_opp_bars": 3,
            "rrr": 2.0,
            "fib_level": 0.5,
            "entry_mode": "market_fallback",
            "symbol": "EURUSD",
            "sl_fib_level": 0.8,
            "wave_plus": True,
            "ext_enabled": True,
            "ext_wave_min_pct": 0.76,
            "ext_counter_enabled": True,
            "ext_trade_both_sides_in_range": True,
            "trend_filter_enabled": False,
            "wave_position_enabled": False,
            "pp_enabled": False,
        }
    )
    df = pd.read_csv("data/EURUSD_M30.csv", parse_dates=["datetime"])
    df = df.rename(columns={"datetime": "time"})
    df = df[(df["time"] >= "2026-03-18") & (df["time"] <= "2026-03-21")].reset_index(
        drop=True
    )
    eng = BacktestEngine(cfg)
    closed = eng.run(df, retain_wave_snapshot=True)
    wt = "202603192200"
    assert eng._ext_forming_first_bar.get(wt, 99) < eng.wave_birth_by_time.get(wt, 0)
    assert eng.wave_debug.get("ext_counter_time_placed", 0) >= 1
    ct = [
        t for t in closed
        if str(getattr(t, "entry_tag", "")) == "ext_counter_time"
        and str(t.wave_time) == wt
    ]
    assert ct
    assert pd.Timestamp(ct[0].entry_time).strftime("%Y-%m-%d %H:%M") == "2026-03-19 21:00"
