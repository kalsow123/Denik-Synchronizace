"""Live WAVE stats — parita s backtest wave_isolation_study reportem."""
from __future__ import annotations

from config.bot_config import BotConfig
from runtime.live_wave_stats import (
    LiveWaveStatsTracker,
    maybe_emit_live_wave_summary,
    position_kind_from_mt5_comment,
)


def test_position_kind_wave_comment():
    assert position_kind_from_mt5_comment("W202604291430") == "WAVE"


def test_position_kind_counter():
    assert position_kind_from_mt5_comment("CNTR_202604291430@G4") == "WAVE_COUNTER"


def test_position_kind_ext_secondary():
    assert position_kind_from_mt5_comment("E23_202604291430") == "EXT"


def test_live_wave_stats_tracker_wave_only():
    tr = LiveWaveStatsTracker()
    assert tr.on_position_closed(comment="W202604291430", pnl_usd=100.0) == "WAVE"
    assert tr.on_position_closed(comment="CNTR_x", pnl_usd=-50.0) == "WAVE_COUNTER"
    assert tr.wave_closes == 1
    assert tr.wave_pnl_usd == 100.0
    assert tr.other_closes == 1


def test_maybe_emit_live_wave_summary():
    cfg = BotConfig()
    tr = LiveWaveStatsTracker()
    tr.on_position_closed(comment="W202604291430", pnl_usd=10.0)
    last = maybe_emit_live_wave_summary(cfg, tr, last_emit_wave_closes=0)
    assert last == 1
    assert maybe_emit_live_wave_summary(cfg, tr, last_emit_wave_closes=last) == 1
