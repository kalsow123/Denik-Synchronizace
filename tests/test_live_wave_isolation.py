"""Live MT5 wave isolation — varianta B (study, MT5 jen WAVE)."""
from __future__ import annotations

from config.bot_config import LIVE_BOT_CONFIG
from runtime.live_wave_isolation import (
    classify_live_execution_mode,
    filter_wave_only_pending_snapshots,
    guard_live_send_order,
    is_isolation_study_allowed_mt5_comment,
    live_wave_isolation_mt5_active,
    resolve_live_execution_config,
    skip_live_non_wave_entry,
)


def test_live_isolation_active_for_combo2():
    cfg = resolve_live_execution_config(LIVE_BOT_CONFIG)
    assert live_wave_isolation_mt5_active(cfg)
    assert classify_live_execution_mode(cfg) == "wave_study_wave_only"


def test_apply_keeps_engine_counter_routing():
    cfg = resolve_live_execution_config(LIVE_BOT_CONFIG)
    assert cfg.live_mt5_wave_slice_only is True
    assert cfg.counter_position_enabled is True
    assert cfg.wave_counter_two_sided_enabled is True
    assert cfg.ext_counter_enabled is True
    assert cfg.ext_enabled is True
    assert cfg.pp_enabled is False
    assert cfg.bos_entry_enable is False


def test_guard_blocks_non_wave_and_two_sided():
    cfg = resolve_live_execution_config(LIVE_BOT_CONFIG)
    ext = {
        "wave_time": "202601011000",
        "dir": 1,
        "fib50": 1.1,
        "sl": 1.09,
        "move_pct": 0.8,
        "is_ext": True,
    }
    assert guard_live_send_order(cfg, ext) is True
    assert guard_live_send_order(cfg, ext, bypass_trend_filter=True) is True
    plain = {
        "wave_time": "202601011000",
        "dir": 1,
        "fib50": 1.1,
        "sl": 1.09,
        "move_pct": 0.30,
    }
    assert guard_live_send_order(cfg, plain) is False
    assert guard_live_send_order(cfg, plain, is_two_sided_mirror=True) is True


def test_skip_blocks_counter_ext_two_sided():
    cfg = resolve_live_execution_config(LIVE_BOT_CONFIG)
    assert skip_live_non_wave_entry(cfg, "WAVE") is False
    assert skip_live_non_wave_entry(cfg, "COUNTER") is True
    assert skip_live_non_wave_entry(cfg, "EXT_COUNTER") is True
    assert skip_live_non_wave_entry(cfg, "TWO_SIDED") is True
    assert skip_live_non_wave_entry(cfg, "PP") is True


def test_allowed_mt5_comments_wave_only():
    assert is_isolation_study_allowed_mt5_comment("W202601011000")
    assert not is_isolation_study_allowed_mt5_comment("CNTR_202601011000@G4")
    assert not is_isolation_study_allowed_mt5_comment("ECT_202601011000")
    assert not is_isolation_study_allowed_mt5_comment("PP_202601011000")


def test_snapshot_filter_wave_only():
    from infra.pending_snapshot import PendingOrderSnapshot

    cfg = resolve_live_execution_config(LIVE_BOT_CONFIG)
    snaps = [
        PendingOrderSnapshot(2, 1.1, 1.09, 1.12, 0.1, "W202601011000", None),
        PendingOrderSnapshot(2, 1.1, 1.09, 1.12, 0.1, "CNTR_202601011000@G4", None),
        PendingOrderSnapshot(2, 1.1, 1.09, 1.12, 0.1, "PP_202601011000", None),
    ]
    out = filter_wave_only_pending_snapshots(cfg, snaps)
    assert len(out) == 1
    assert out[0].comment == "W202601011000"
