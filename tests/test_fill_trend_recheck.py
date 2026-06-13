"""Trend re-check at pending fill and MARKET fallback."""
from __future__ import annotations

from config.bot_config import BotConfig
from config.enums import EntryMode, TPMode
from strategy.trend_bos import TrendState, entry_allowed_at_fill_bar, wave_allowed_for_fill_direction


def _cfg() -> BotConfig:
    return BotConfig(
        wave_min_pct=0.26,
        min_opp_bars=3,
        trend_filter_enabled=True,
        trend_hh_hl_filter_enabled=False,
        entry_mode=EntryMode.MARKET_FALLBACK,
        tp_mode=TPMode.WAVE_TARGET_N,
    )


def test_entry_allowed_blocks_counter_trend_at_fill_bar():
    cfg = _cfg()
    wave = {"dir": -1, "wave_time": "202603250100", "box_top": 1.1, "box_bottom": 1.0}
    ts = TrendState(direction="bull")
    allowed, reason = entry_allowed_at_fill_bar(wave, ts, cfg)
    assert allowed is False
    assert reason == "wave_against_trend"


def test_entry_allowed_passes_with_trend_at_fill_bar():
    cfg = _cfg()
    wave = {"dir": 1, "wave_time": "202603250100", "box_top": 1.1, "box_bottom": 1.0}
    ts = TrendState(direction="bull")
    allowed, reason = entry_allowed_at_fill_bar(wave, ts, cfg)
    assert allowed is True
    assert reason == "passed"


def test_entry_allowed_bypass_for_bos_reentry():
    cfg = _cfg()
    wave = {"dir": 1, "wave_time": "202603250100", "box_top": 1.1, "box_bottom": 1.0}
    ts = TrendState(direction="bear")
    allowed, reason = entry_allowed_at_fill_bar(
        wave, ts, cfg, is_bos_reentry=True,
    )
    assert allowed is True
    assert reason == "trend_bypass"


def test_entry_allowed_bypass_flag_for_retro_bos_market():
    cfg = _cfg()
    wave = {"dir": 1, "wave_time": "202603250100", "box_top": 1.1, "box_bottom": 1.0}
    ts = TrendState(direction="bear")
    allowed, reason = entry_allowed_at_fill_bar(
        wave, ts, cfg, bypass_trend_filter=True,
    )
    assert allowed is True
    assert reason == "trend_bypass"


def test_fill_direction_passes_without_hh_hl_at_fill_bar():
    """HH/HL selhání na fill baru nesmí blokovat — jen wave_against_trend."""
    cfg = BotConfig(
        wave_min_pct=0.26,
        min_opp_bars=3,
        trend_filter_enabled=True,
        trend_hh_hl_filter_enabled=True,
        entry_mode=EntryMode.MARKET_FALLBACK,
        tp_mode=TPMode.WAVE_TARGET_N,
    )
    # UP vlna v bull trendu, ale box neni HH+HL vuci predchozi strukture
    wave = {"dir": 1, "wave_time": "202603250100", "box_top": 1.05, "box_bottom": 1.0}
    ts = TrendState(
        direction="bull",
        last_up_box_top=1.10,
        last_up_box_bottom=1.02,
    )
    allowed, reason = wave_allowed_for_fill_direction(wave, ts, cfg)
    assert allowed is True
    assert reason == "passed"
