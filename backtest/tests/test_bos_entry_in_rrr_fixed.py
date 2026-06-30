"""BOS entry (WAVE_BOS) v tp_mode=rrr_fixed pres bos_entry_in_rrr_fixed."""
from __future__ import annotations

import pandas as pd

from backtest.engine import BacktestEngine
from backtest.grid.translator import grid_dict_to_bot_config
from config.enums import TPMode
from strategy.trend_bos import (
    bos_entry_in_rrr_fixed_enabled,
    bos_entry_should_open_on_flip,
    bos_flip_handler_should_run,
)


def _mini_df() -> pd.DataFrame:
    times = pd.date_range("2026-03-03 00:00", periods=200, freq="30min")
    return pd.DataFrame(
        {
            "time": times,
            "open": 1.10,
            "high": 1.11,
            "low": 1.09,
            "close": 1.10,
        }
    )


def _base_grid() -> dict:
    return {
        "timeframe": "M30",
        "wave_min_pct": 0.26,
        "min_opp_bars": 3,
        "rrr": 2.0,
        "fib_level": 0.5,
        "entry_mode": "market_fallback",
        "symbol": "EURUSD",
        "sl_fib_level": 0.8,
        "abort_fib_level": "shift_sl",
        "wave_plus": True,
        "trend_filter_enabled": True,
        "trend_hh_hl_filter_enabled": True,
        "tp_mode": "rrr_fixed",
        "pending_cancel_mode": "number",
        "bos_entry_enable": False,
        "wave_position_enabled": False,
        "pp_enabled": False,
        "ext_enabled": False,
        "counter_position_enabled": False,
    }


def test_bos_entry_in_rrr_fixed_disabled_by_default():
    cfg = grid_dict_to_bot_config(_base_grid())
    assert cfg.bos_entry_in_rrr_fixed is False
    assert bos_entry_in_rrr_fixed_enabled(cfg) is False
    assert bos_entry_should_open_on_flip(cfg) is False


def test_rrr_fixed_pcm_number_opens_bos_reentry_when_flag_on():
    cfg = grid_dict_to_bot_config(
        {**_base_grid(), "bos_entry_in_rrr_fixed": True}
    )
    assert cfg.tp_mode == TPMode.RRR_FIXED
    assert bos_entry_should_open_on_flip(cfg) is True
    assert bos_flip_handler_should_run(cfg, close_pos=False, cancel_pend=False) is True

    df = pd.read_csv(
        "data/EURUSD_M30.csv",
        parse_dates=["datetime"],
    )
    df = df.rename(columns={"datetime": "time"})
    df = df[(df["time"] >= "2026-03-03") & (df["time"] <= "2026-05-10")].reset_index(
        drop=True
    )
    eng = BacktestEngine(cfg)
    eng.run(df)
    assert int(eng.wave_debug.get("bos_reentry_positions_opened", 0)) > 0


def test_rrr_fixed_pcm_number_zero_reentry_when_flag_off():
    cfg = grid_dict_to_bot_config(_base_grid())
    df = pd.read_csv(
        "data/EURUSD_M30.csv",
        parse_dates=["datetime"],
    )
    df = df.rename(columns={"datetime": "time"})
    df = df[(df["time"] >= "2026-03-03") & (df["time"] <= "2026-05-10")].reset_index(
        drop=True
    )
    eng = BacktestEngine(cfg)
    eng.run(df)
    assert int(eng.wave_debug.get("bos_reentry_positions_opened", 0)) == 0


def test_flag_ignored_outside_rrr_fixed():
    cfg = grid_dict_to_bot_config(
        {
            **_base_grid(),
            "tp_mode": "bos_exit",
            "bos_entry_in_rrr_fixed": True,
            "bos_entry_enable": False,
        }
    )
    assert bos_entry_in_rrr_fixed_enabled(cfg) is False
    assert bos_entry_should_open_on_flip(cfg) is False
