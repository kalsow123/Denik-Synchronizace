from __future__ import annotations

import re
from pathlib import Path

import pytest

from backtest.grid.translator import grid_dict_to_bot_config
from config.enums import TPMode, TpWaveEarlyMode, TpWaveExitOn, TpWaveIntrabarPriority
from strategy.trend_bos import tp_mode_uses_bos_per_bar_exit
from strategy.wave_sequence import compute_wave_counter_take_profit
from strategy.wave_target_n_mode import (
    WAVE_TARGET_N_FAMILY,
    is_wave_target_n_family,
    is_wave_target_n_g,
    is_wave_target_n_legacy,
)


def _base(**overrides):
    d = {
        "timeframe": "M30",
        "wave_min_pct": 0.26,
        "min_opp_bars": 3,
        "rrr": 2.0,
        "fib_level": 0.5,
        "entry_mode": "market_fallback",
        "symbol": "EURUSD.x",
        "tp_mode": "wave_target_n",
        "tp_target_wave_index": 4,
        "wave_extension_pct": 0.20,
    }
    d.update(overrides)
    return grid_dict_to_bot_config(d)


def test_wave_target_n_g_preset_from_translator():
    cfg = _base(tp_mode="wave_target_n_g")
    assert cfg.tp_mode == TPMode.WAVE_TARGET_N_G
    assert cfg.tp_wave_early_mode == TpWaveEarlyMode.FORMING_QUALIFIED
    assert cfg.tp_wave_exit_on == TpWaveExitOn.EXTENSION_HIT
    assert cfg.tp_wave_early_fallback_birth is True
    assert is_wave_target_n_g(cfg)
    assert is_wave_target_n_family(cfg)
    assert not is_wave_target_n_legacy(cfg)


def test_wave_target_n_legacy_default():
    cfg = _base()
    assert cfg.tp_mode == TPMode.WAVE_TARGET_N
    assert is_wave_target_n_family(cfg)
    assert is_wave_target_n_legacy(cfg)
    assert not is_wave_target_n_g(cfg)


def test_wave_target_n_manual_g_flags_equivalent_to_g_mode():
    cfg_flags = _base(
        tp_wave_early_mode="forming_qualified",
        tp_wave_exit_on="extension_hit",
    )
    cfg_g = _base(tp_mode="wave_target_n_g")
    assert is_wave_target_n_g(cfg_flags) == is_wave_target_n_g(cfg_g)
    assert cfg_flags.tp_wave_early_mode == cfg_g.tp_wave_early_mode
    assert cfg_flags.tp_wave_exit_on == cfg_g.tp_wave_exit_on


def test_family_includes_bos_and_counter_helpers():
    cfg_g = _base(tp_mode="wave_target_n_g")
    assert tp_mode_uses_bos_per_bar_exit(cfg_g) is True
    assert compute_wave_counter_take_profit(cfg_g, 1.12, 1.13, is_buy=False) is None


def test_no_raw_wave_target_n_checks_outside_whitelist():
    root = Path(__file__).resolve().parents[2]
    scan_files = [
        root / "backtest" / "engine.py",
        root / "runtime" / "live_loop.py",
        root / "infra" / "orders.py",
        root / "strategy" / "wave_sequence.py",
        root / "strategy" / "wave_target_n_early.py",
    ]
    bad = re.compile(
        r"(?<![_G])TPMode\.WAVE_TARGET_N\b(?!\s*,\s*TPMode\.WAVE_TARGET_N_G)"
        r"|(?<![_g])['\"]wave_target_n['\"]"
    )
    offenders: list[str] = []
    for path in scan_files:
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            s = line.strip()
            if s.startswith("#"):
                continue
            if "is_wave_target_n" in line or "wave_target_n_g" in line or "WAVE_TARGET_N_G" in line:
                continue
            if bad.search(line):
                offenders.append(f"{path.name}:{i}: {s[:100]}")
    assert not offenders, "\n".join(offenders)


def test_family_enum_covers_both_modes():
    assert TPMode.WAVE_TARGET_N in WAVE_TARGET_N_FAMILY
    assert TPMode.WAVE_TARGET_N_G in WAVE_TARGET_N_FAMILY
