"""Live MT5 wave isolation — engine parita combo 2."""
from __future__ import annotations

from config.bot_config import LIVE_BOT_CONFIG
from runtime.live_wave_isolation import (
    filter_wave_only_pending_snapshots,
    guard_live_send_order,
    is_isolation_study_allowed_mt5_comment,
    live_wave_isolation_mt5_active,
    resolve_live_execution_config,
)


def test_live_isolation_active_for_combo2():
    cfg = resolve_live_execution_config(LIVE_BOT_CONFIG)
    assert live_wave_isolation_mt5_active(cfg)


def test_apply_keeps_engine_counter_and_ext_counter():
    cfg = resolve_live_execution_config(LIVE_BOT_CONFIG)
    assert cfg.live_mt5_wave_slice_only is True
    assert cfg.counter_position_enabled is True
    assert cfg.wave_counter_two_sided_enabled is True
    assert cfg.ext_counter_enabled is True
    assert cfg.ext_secondary_enabled is False
    assert cfg.pp_enabled is False
    assert cfg.bos_entry_enable is False
    assert cfg.ext_enabled is True


def test_guard_blocks_ext_and_bos_not_counter():
    cfg = resolve_live_execution_config(LIVE_BOT_CONFIG)
    wave = {
        "wave_time": "202601011000",
        "dir": 1,
        "fib50": 1.1,
        "sl": 1.09,
        "move_pct": 0.8,
        "is_ext": True,
    }
    assert guard_live_send_order(cfg, wave) is True
    assert guard_live_send_order(
        cfg, wave, bypass_trend_filter=True,
    ) is True
    plain = {
        "wave_time": "202601011000",
        "dir": 1,
        "fib50": 1.1,
        "sl": 1.09,
        "move_pct": 0.30,
    }
    assert guard_live_send_order(cfg, plain) is False
    assert guard_live_send_order(
        cfg, plain, is_two_sided_mirror=True,
    ) is False


def test_allowed_mt5_comments():
    assert is_isolation_study_allowed_mt5_comment("W202601011000")
    assert is_isolation_study_allowed_mt5_comment("CNTR_202601011000@G4")
    assert is_isolation_study_allowed_mt5_comment("ECT_202601011000")
    assert is_isolation_study_allowed_mt5_comment("ECB_202601011000")
    assert not is_isolation_study_allowed_mt5_comment("PP_202601011000")
    assert not is_isolation_study_allowed_mt5_comment("E23_202601011000")


def test_snapshot_filter_engine_aligned():
    from infra.pending_snapshot import PendingOrderSnapshot

    cfg = resolve_live_execution_config(LIVE_BOT_CONFIG)
    snaps = [
        PendingOrderSnapshot(2, 1.1, 1.09, 1.12, 0.1, "W202601011000", None),
        PendingOrderSnapshot(2, 1.1, 1.09, 1.12, 0.1, "CNTR_202601011000@G4", None),
        PendingOrderSnapshot(2, 1.1, 1.09, 1.12, 0.1, "PP_202601011000", None),
    ]
    out = filter_wave_only_pending_snapshots(cfg, snaps)
    assert len(out) == 2
    assert {s.comment for s in out} == {"W202601011000", "CNTR_202601011000@G4"}
