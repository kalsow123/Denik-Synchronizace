"""Live MT5 execution modes — parita s grid backtesterem."""
from __future__ import annotations

from dataclasses import replace

from config.bot_config import BotConfig, LIVE_BOT_CONFIG
from runtime.live_wave_isolation import (
    apply_live_mt5_wave_slice_execution,
    classify_live_execution_mode,
    guard_live_send_order,
    live_wave_isolation_mt5_active,
    live_wave_isolation_requested,
    resolve_live_execution_config,
)


def test_classify_wave_study_variant_b():
    cfg = resolve_live_execution_config(LIVE_BOT_CONFIG)
    assert classify_live_execution_mode(cfg) == "wave_study_wave_only"
    assert live_wave_isolation_mt5_active(cfg)


def test_classify_wave_only():
    raw = replace(
        LIVE_BOT_CONFIG,
        wave_isolation_study=False,
        wave_positions_only=True,
        wave_counter_two_sided_enabled=True,
        ext_enabled=True,
    )
    cfg = resolve_live_execution_config(raw)
    assert classify_live_execution_mode(cfg) == "wave_only"
    assert not live_wave_isolation_mt5_active(cfg)
    assert cfg.counter_position_enabled is False
    assert cfg.ext_enabled is False
    assert cfg.wave_position_enabled is True


def test_classify_full_engine():
    raw = replace(
        LIVE_BOT_CONFIG,
        wave_positions_only=False,
        wave_isolation_study=False,
        wave_counter_two_sided_enabled=True,
        ext_enabled=True,
        ext_counter_enabled=True,
    )
    cfg = resolve_live_execution_config(raw)
    assert classify_live_execution_mode(cfg) == "full"
    assert not live_wave_isolation_mt5_active(cfg)
    assert cfg.counter_position_enabled is True
    assert cfg.ext_enabled is True
    assert guard_live_send_order(cfg, {"wave_time": "202601011000", "dir": 1, "move_pct": 0.3}) is False


def test_classify_wave_disabled():
    raw = replace(
        LIVE_BOT_CONFIG,
        wave_position_enabled=False,
        wave_positions_only=False,
        wave_isolation_study=False,
        wave_counter_two_sided_enabled=True,
    )
    cfg = resolve_live_execution_config(raw)
    assert classify_live_execution_mode(cfg) == "wave_disabled"
    assert not live_wave_isolation_mt5_active(cfg)


def test_slice_keeps_engine_counter_ext_counter():
    raw = LIVE_BOT_CONFIG
    engine = resolve_live_execution_config(raw)
    assert live_wave_isolation_requested(raw)
    assert engine.live_mt5_wave_slice_only is True
    assert engine.counter_position_enabled is True
    assert engine.ext_counter_enabled is True
    assert engine.ext_enabled is True
    assert engine.pp_enabled is False


def test_apply_disables_pp_bos_ext_secondary_only():
    from config.position_modes import resolve_grid_engine_config

    raw = LIVE_BOT_CONFIG
    engine = resolve_grid_engine_config(raw)
    assert engine.counter_position_enabled is True
    exec_cfg = apply_live_mt5_wave_slice_execution(
        engine, requested=live_wave_isolation_requested(raw),
    )
    assert exec_cfg.live_mt5_wave_slice_only is True
    assert exec_cfg.counter_position_enabled is True
    assert exec_cfg.ext_counter_enabled is True
    assert exec_cfg.ext_secondary_enabled is False
    assert exec_cfg.pp_enabled is False
    assert exec_cfg.ext_enabled is True
