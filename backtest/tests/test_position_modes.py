"""config.position_modes — jen klasické WAVE napříč grid / live."""
from __future__ import annotations

from backtest.grid.study_mode import resolve_study_mode
from backtest.grid.translator import grid_dict_to_bot_config
from config.bot_config import BotConfig
from config.position_modes import (
    apply_wave_positions_only_to_bot_config,
    bot_config_is_wave_positions_only,
    grid_is_wave_positions_only,
    plan_grid_position_flags,
)
def test_grid_implicit_wave_positions_only():
    d = {
        "wave_position_enabled": True,
        "wave_counter_two_sided_enabled": False,
        "pp_enabled": False,
        "bos_entry_enable": False,
        "ext_enabled": False,
        "ext_counter_enabled": False,
        "symbol": "EURUSD.x",
        "timeframe": "M30",
        "wave_min_pct": 0.26,
        "min_opp_bars": 3,
        "rrr": 2.0,
        "fib_level": 0.5,
        "entry_mode": "market_fallback",
    }
    assert grid_is_wave_positions_only(d)
    plan = plan_grid_position_flags(d)
    assert plan.wave_positions_only
    assert plan.wave_counter_two_sided_enabled is False
    assert plan.pp_enabled is False


def test_grid_wave_isolation_study_enables_engine_counter():
    d = {
        "wave_positions_only": True,
        "wave_isolation_study": True,
        "wave_counter_two_sided_enabled": False,
        "pp_enabled": True,
        "bos_entry_enable": True,
        "ext_enabled": True,
        "symbol": "EURUSD.x",
        "timeframe": "M30",
        "wave_min_pct": 0.26,
        "min_opp_bars": 3,
        "rrr": 2.0,
        "fib_level": 0.5,
        "entry_mode": "market_fallback",
    }
    plan = plan_grid_position_flags(d)
    assert plan.wave_counter_two_sided_enabled is True
    assert plan.pp_enabled is True
    assert plan.ext_enabled is True


def test_live_normalizer_forces_modules_off():
    cfg = BotConfig(
        wave_positions_only=True,
        wave_position_enabled=True,
        wave_counter_two_sided_enabled=True,
        pp_enabled=True,
        bos_entry_enable=True,
        ext_enabled=True,
    )
    out = apply_wave_positions_only_to_bot_config(cfg)
    assert out.wave_counter_two_sided_enabled is False
    assert out.pp_enabled is False
    assert out.bos_entry_enable is False
    assert out.ext_enabled is False
    assert out.wave_isolation_study is False


def test_live_implicit_wave_only_from_flags():
    cfg = BotConfig(
        wave_position_enabled=True,
        wave_counter_two_sided_enabled=False,
        pp_enabled=False,
        bos_entry_enable=False,
        ext_enabled=False,
        ext_counter_enabled=False,
    )
    assert bot_config_is_wave_positions_only(cfg)
    out = apply_wave_positions_only_to_bot_config(cfg)
    assert out.wave_positions_only is True


def test_translator_wave_isolation_matches_full_counter():
    base = {
        "symbol": "EURUSD.x",
        "timeframe": "M30",
        "wave_min_pct": 0.26,
        "min_opp_bars": 3,
        "rrr": 2.0,
        "fib_level": 0.5,
        "entry_mode": "market_fallback",
        "wave_counter_two_sided_enabled": True,
        "skip_primary_entry_on_parent_wave_enable": True,
        "tp_mode": "bos_exit",
    }
    iso = dict(base)
    iso["wave_positions_only"] = True
    iso["wave_isolation_study"] = True
    iso["wave_counter_two_sided_enabled"] = False
    assert grid_dict_to_bot_config(base).wave_counter_two_sided_enabled is True
    assert grid_dict_to_bot_config(iso).wave_counter_two_sided_enabled is True


def test_study_mode_wave_only_vs_isolation():
    assert resolve_study_mode({"wave_positions_only": True}) == "wave_only"
    assert (
        resolve_study_mode(
            {"wave_positions_only": True, "wave_isolation_study": True}
        )
        == "wave_isolation"
    )
