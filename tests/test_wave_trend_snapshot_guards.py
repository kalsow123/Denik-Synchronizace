"""Prompt 5: replay / main-loop trend snapshot + post-EXT counter guards."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from config.bot_config import BotConfig
from config.enums import TPMode
from runtime.live_loop import _maybe_place_live_counter_from_tp
from strategy.trend_bos import TrendState, wave_allowed_for_entry


def _cfg_trend_on() -> BotConfig:
    return BotConfig(
        wave_min_pct=0.26,
        min_opp_bars=3,
        trend_filter_enabled=True,
        trend_hh_hl_filter_enabled=True,
        counter_position_enabled=True,
        tp_target_wave_index=2,
        tp_mode=TPMode.BOS_EXIT,
    )


def test_wave_allowed_blocks_missing_trend_snapshot():
    cfg = _cfg_trend_on()
    wave = {"dir": 1, "wave_time": "202603051800", "box_top": 1.1, "box_bottom": 1.0}
    allowed, reason = wave_allowed_for_entry(wave, None, cfg)
    assert allowed is False
    assert reason == "no_trend_state"


def test_counter_skipped_when_post_ext_suppressed():
    cfg = _cfg_trend_on()
    wave = {
        "dir": 1,
        "wave_time": "202603051800",
        "fib50": 1.05,
        "sl": 1.0,
        "tp": 1.1,
        "post_ext_trend_suppressed": True,
    }
    seq_info = {"202603051800": MagicMock(index_in_trend=2)}
    with patch("runtime.live_loop.log_event") as log_event:
        with patch("runtime.live_loop._place_live_counter_position") as place:
            _maybe_place_live_counter_from_tp(
                cfg=cfg,
                wave=wave,
                seq_info=seq_info,
                tp_price=1.1,
                all_waves=[wave],
            )
    log_event.assert_called_once()
    assert log_event.call_args[0][2] == "COUNTER_SKIPPED_POST_EXT_SUPPRESSED"
    place.assert_not_called()


def test_counter_placed_when_not_suppressed_and_tp_wave():
    cfg = _cfg_trend_on()
    wave = {
        "dir": 1,
        "wave_time": "202603051800",
        "fib50": 1.05,
        "sl": 1.0,
        "tp": 1.1,
    }
    info = MagicMock(index_in_trend=2, prev_same_dir_in_trend_wave_time=None)
    seq_info = {"202603051800": info}
    with patch("runtime.live_loop.log_event"):
        with patch("runtime.live_loop._place_live_counter_position") as place:
            _maybe_place_live_counter_from_tp(
                cfg=cfg,
                wave=wave,
                seq_info=seq_info,
                tp_price=1.1,
                all_waves=[wave],
            )
    place.assert_called_once()


def test_counter_skipped_for_wave_target_n_g():
    cfg = BotConfig(
        wave_min_pct=0.26,
        min_opp_bars=3,
        counter_position_enabled=True,
        tp_target_wave_index=4,
        tp_mode=TPMode.WAVE_TARGET_N_G,
    )
    wave = {
        "dir": 1,
        "wave_time": "202603051800",
        "fib50": 1.05,
        "sl": 1.0,
        "tp": 1.1,
        "wave_target_tp_price": 1.12,
    }
    info = MagicMock(index_in_trend=4, prev_same_dir_in_trend_wave_time="prev")
    seq_info = {"202603051800": info}
    with patch("runtime.live_loop._place_live_counter_position") as place:
        _maybe_place_live_counter_from_tp(
            cfg=cfg,
            wave=wave,
            seq_info=seq_info,
            tp_price=1.12,
            all_waves=[wave],
        )
    place.assert_not_called()


def test_wave_allowed_passes_with_snapshot():
    cfg = _cfg_trend_on()
    wave = {"dir": 1, "wave_time": "202603051800", "box_top": 1.1, "box_bottom": 1.0}
    ts = TrendState(direction="bull")
    allowed, reason = wave_allowed_for_entry(wave, ts, cfg)
    assert allowed is True
    assert reason in ("passed", "first_in_trend")
