"""Tests for live WAVE_TARGET_N / G state sync (restart parity)."""
from __future__ import annotations

import pandas as pd

from backtest.grid.translator import grid_dict_to_bot_config
from runtime.wave_target_n_live import sync_wave_target_n_live_state
from strategy.wave_sequence import WaveSequenceInfo, is_tp_wave_index


def _cfg_g(**overrides):
    d = {
        "timeframe": "M30",
        "wave_min_pct": 0.26,
        "min_opp_bars": 3,
        "rrr": 2.0,
        "fib_level": 0.5,
        "entry_mode": "market_fallback",
        "symbol": "EURUSD.x",
        "sl_fib_level": 0.8,
        "wave_plus": True,
        "risk_usd": 500.0,
        "contract_size": 100_000.0,
        "tp_mode": "wave_target_n_g",
        "tp_target_wave_index": 4,
        "wave_extension_pct": 0.10,
    }
    d.update(overrides)
    return grid_dict_to_bot_config(d)


def test_processed_tp_wave_before_last_bar():
    cfg = _cfg_g()
    df = pd.DataFrame(
        {
            "time": pd.date_range("2026-03-01", periods=5, freq="30min"),
            "open": [1.1, 1.1, 1.1, 1.1, 1.1],
            "high": [1.11, 1.11, 1.11, 1.11, 1.11],
            "low": [1.09, 1.09, 1.09, 1.09, 1.09],
            "close": [1.10, 1.10, 1.10, 1.10, 1.10],
        }
    )
    last_bar = len(df) - 1
    wt_tp = "202603011200"
    wt_prev = "202603011000"
    waves = [
        {"wave_time": wt_prev, "dir": 1, "box_top": 1.12, "box_bottom": 1.10, "draw_right": 2},
        {"wave_time": wt_tp, "dir": 1, "box_top": 1.13, "box_bottom": 1.11, "draw_right": 3},
    ]
    seq_info = {
        wt_prev: WaveSequenceInfo(index_in_trend=3, prev_same_dir_in_trend_wave_time=None),
        wt_tp: WaveSequenceInfo(index_in_trend=4, prev_same_dir_in_trend_wave_time=wt_prev),
    }
    birth = {wt_prev: 2, wt_tp: 3}
    sync = sync_wave_target_n_live_state(
        cfg, df, waves, seq_info,
        birth_by_time=birth,
        last_bar_idx=last_bar,
        active_counter_wave_times=set(),
    )
    assert wt_tp in sync.processed_tp_wave_times
    assert sync.forming_tp_watch is None


def test_forming_watch_rebuilt_after_w3():
    cfg = _cfg_g()
    # W3 born bar 1, last bar 4 — replay bars 2..3
    df = pd.DataFrame(
        {
            "time": pd.date_range("2026-03-01", periods=5, freq="30min"),
            "open": [1.1000, 1.1000, 1.1020, 1.1040, 1.1050],
            "high": [1.1000, 1.1000, 1.1050, 1.1080, 1.1090],
            "low": [1.1000, 1.1000, 1.1010, 1.1030, 1.1040],
            "close": [1.1000, 1.1000, 1.1040, 1.1070, 1.1080],
        }
    )
    wt_w3 = "w3"
    waves = [
        {
            "wave_time": wt_w3,
            "dir": 1,
            "box_top": 1.1100,
            "box_bottom": 1.1000,
            "move_pct": 0.68,
            "draw_right": 1,
        },
    ]
    seq_info = {
        wt_w3: WaveSequenceInfo(index_in_trend=3, prev_same_dir_in_trend_wave_time="w2"),
    }
    sync = sync_wave_target_n_live_state(
        cfg, df, waves, seq_info,
        birth_by_time={wt_w3: 1},
        last_bar_idx=4,
        active_counter_wave_times=set(),
    )
    assert wt_w3 not in sync.processed_tp_wave_times
    assert sync.forming_tp_watch is not None
    assert sync.forming_tp_watch.target_tp_index == 4
    assert is_tp_wave_index(4, 4)


def test_counter_placed_from_mt5_key():
    cfg = _cfg_g()
    df = pd.DataFrame(
        {
            "time": pd.date_range("2026-03-01", periods=3, freq="30min"),
            "open": [1.1, 1.1, 1.1],
            "high": [1.11, 1.11, 1.11],
            "low": [1.09, 1.09, 1.09],
            "close": [1.10, 1.10, 1.10],
        }
    )
    wt_w3 = "202603011000"
    waves = [
        {
            "wave_time": wt_w3,
            "dir": 1,
            "box_top": 1.11,
            "box_bottom": 1.10,
            "draw_right": 0,
        },
    ]
    seq_info = {
        wt_w3: WaveSequenceInfo(index_in_trend=3, prev_same_dir_in_trend_wave_time=None),
    }
    g_key = f"{wt_w3}@G4"
    sync = sync_wave_target_n_live_state(
        cfg, df, waves, seq_info,
        birth_by_time={wt_w3: 0},
        last_bar_idx=2,
        active_counter_wave_times={g_key},
    )
    assert sync.forming_tp_watch is not None
    assert sync.forming_tp_watch.counter_placed is True
    assert sync.forming_tp_watch.counter_wave_time_key == g_key
