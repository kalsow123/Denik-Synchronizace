"""Tests for unified WAVE_TARGET_N / G per-bar cycle."""
from __future__ import annotations

from unittest.mock import patch

import pandas as pd

from backtest.grid.translator import grid_dict_to_bot_config
from runtime.wave_target_n_bar import run_wave_target_n_bar_cycle
from strategy.wave_sequence import WaveSequenceInfo, is_tp_wave_index


def _cfg_g(**overrides):
    d = {
        "timeframe": "M30",
        "wave_min_pct": 0.26,
        "min_opp_bars": 3,
        "rrr": 2.0,
        "fib_level": 0.5,
        "entry_mode": "market_fallback",
        "symbol": "EURUSD",
        "sl_fib_level": 0.8,
        "wave_plus": True,
        "risk_usd": 500.0,
        "contract_size": 100_000.0,
        "tp_mode": "wave_target_n_g",
        "tp_target_wave_index": 4,
        "wave_extension_pct": 0.10,
        "counter_position_enabled": True,
    }
    d.update(overrides)
    return grid_dict_to_bot_config(d)


def test_g_same_bar_extension_skips_tp_wave_close():
    cfg = _cfg_g()
    df = pd.DataFrame(
        {
            "time": pd.date_range("2026-03-01", periods=4, freq="30min"),
            "open": [1.1, 1.1, 1.1, 1.1],
            "high": [1.11, 1.11, 1.11, 1.11],
            "low": [1.09, 1.09, 1.09, 1.09],
            "close": [1.10, 1.10, 1.10, 1.10],
        }
    )
    wt_tp = "202603011200"
    waves = [
        {
            "wave_time": wt_tp,
            "dir": 1,
            "box_top": 1.13,
            "box_bottom": 1.11,
            "draw_right": 3,
        },
    ]
    seq_info = {
        wt_tp: WaveSequenceInfo(index_in_trend=4, prev_same_dir_in_trend_wave_time="w3"),
    }
    processed: set[str] = set()
    tp_close_calls: list[str] = []

    def _fake_tp_close(cfg, *, current_wave_time, **_kw):
        tp_close_calls.append(str(current_wave_time))
        return {
            "trend_dir_closed": 1,
            "wave_counter_closed": 0,
            "two_sided_closed": 0,
            "sl_protected": 0,
        }

    with patch(
        "runtime.wave_target_n_bar.sync_wave_target_n_live_state",
    ) as mock_sync, patch(
        "runtime.wave_target_n_bar.close_positions_on_tp_wave_n",
        side_effect=_fake_tp_close,
    ), patch(
        "runtime.wave_target_n_bar.close_positions_on_extension_tp_hit",
        return_value={
            "trend_dir_closed": 1,
            "wave_counter_closed": 0,
            "two_sided_closed": 0,
            "sl_protected": 0,
        },
    ):
        from runtime.wave_target_n_live import WaveTargetNLiveSync
        from strategy.wave_target_n_early import FormingTpWatch

        watch = FormingTpWatch(
            trend_dir=1,
            prev_wave={"wave_time": "w3", "dir": 1, "box_top": 1.12, "box_bottom": 1.10},
            target_tp_index=4,
            start_bar=2,
            pivot=1.12,
            extreme=1.13,
            armed=True,
            armed_tp=1.125,
        )
        mock_sync.return_value = WaveTargetNLiveSync(
            processed_tp_wave_times=set(),
            forming_tp_watch=watch,
            catch_up_extension=True,
            catch_up_bar=3,
            catch_up_high=1.11,
            catch_up_low=1.09,
            catch_up_close=1.10,
            catch_up_open=1.10,
            catch_up_armed_tp=1.125,
            catch_up_trend_dir=1,
        )
        result = run_wave_target_n_bar_cycle(
            cfg=cfg,
            df=df,
            waves=waves,
            seq_info=seq_info,
            bar_idx=3,
            birth_by_time={wt_tp: 3},
            active_counter_wave_times=set(),
            processed_tp_wave_times=processed,
            forming_tp_watch=watch,
            ext1_per_bar=None,
            current_trend="bull",
            entries_allowed=True,
            bar_high=1.11,
            bar_low=1.09,
            bar_close=1.10,
            bar_open=1.10,
            place_g_extension_counter=lambda **_kw: None,
            g_extension_closed=lambda _s: False,
            place_fallback_counter=lambda **_kw: None,
            log_event_fn=lambda *_a, **_k: None,
        )

    assert wt_tp in processed
    assert tp_close_calls == []
    assert result.g_extension_done is True
    assert is_tp_wave_index(4, 4)


def test_legacy_n_runs_tp_wave_close():
    cfg = _cfg_g(tp_mode="wave_target_n")
    df = pd.DataFrame(
        {
            "time": pd.date_range("2026-03-01", periods=3, freq="30min"),
            "open": [1.1, 1.1, 1.1],
            "high": [1.11, 1.11, 1.11],
            "low": [1.09, 1.09, 1.09],
            "close": [1.10, 1.10, 1.10],
        }
    )
    wt_tp = "202603011200"
    waves = [
        {
            "wave_time": wt_tp,
            "dir": 1,
            "box_top": 1.13,
            "box_bottom": 1.11,
            "draw_right": 2,
        },
    ]
    seq_info = {
        wt_tp: WaveSequenceInfo(index_in_trend=4, prev_same_dir_in_trend_wave_time="w3"),
    }
    processed: set[str] = set()
    tp_close_calls: list[str] = []

    def _fake_tp_close(cfg, *, current_wave_time, **_kw):
        tp_close_calls.append(str(current_wave_time))
        return {
            "trend_dir_closed": 1,
            "wave_counter_closed": 0,
            "two_sided_closed": 0,
            "sl_protected": 0,
        }

    with patch(
        "runtime.wave_target_n_bar.sync_wave_target_n_live_state",
    ) as mock_sync, patch(
        "runtime.wave_target_n_bar.close_positions_on_tp_wave_n",
        side_effect=_fake_tp_close,
    ):
        from runtime.wave_target_n_live import WaveTargetNLiveSync

        mock_sync.return_value = WaveTargetNLiveSync(
            processed_tp_wave_times=set(),
            forming_tp_watch=None,
        )
        run_wave_target_n_bar_cycle(
            cfg=cfg,
            df=df,
            waves=waves,
            seq_info=seq_info,
            bar_idx=2,
            birth_by_time={wt_tp: 2},
            active_counter_wave_times=set(),
            processed_tp_wave_times=processed,
            forming_tp_watch=None,
            ext1_per_bar=None,
            current_trend="bull",
            entries_allowed=True,
            bar_high=1.11,
            bar_low=1.09,
            bar_close=1.10,
            bar_open=1.10,
            place_g_extension_counter=lambda **_kw: None,
            g_extension_closed=lambda _s: False,
            place_fallback_counter=lambda **_kw: None,
            log_event_fn=lambda *_a, **_k: None,
        )

    assert tp_close_calls == [wt_tp]
    assert wt_tp in processed
