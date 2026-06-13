"""
Cache vysledku run_pine_wave_simulation — stejne vlny pro shodne (data + pine cfg).

Pouziti v gridu: kombinace lisici se jen v tp_mode / pp / pending_cancel sdili
detekci vln. Vystup se pred vracenim deep-copy (engine vlny mutuje).
"""
from __future__ import annotations

import copy
from typing import Any

import pandas as pd

from config.bot_config import BotConfig

_CACHE: dict[tuple, tuple] = {}
_CACHE_ENABLED = True


def set_pine_sim_cache_enabled(enabled: bool) -> None:
    global _CACHE_ENABLED
    _CACHE_ENABLED = bool(enabled)


def clear_pine_sim_cache() -> None:
    _CACHE.clear()


def _abort_fib_cache_part(cfg: BotConfig) -> Any:
    val = getattr(cfg, "abort_fib_level", None)
    if val is None:
        return None
    return str(val)


def pine_sim_data_key(df: pd.DataFrame) -> tuple:
    from backtest.ohlc_arrays import ohlc_from_dataframe

    ohlc = ohlc_from_dataframe(df)
    if ohlc.n == 0:
        return (0,)
    t0 = int(pd.Timestamp(ohlc.time_at(0)).value)
    t1 = int(pd.Timestamp(ohlc.time_at(ohlc.n - 1)).value)
    return (int(ohlc.n), t0, t1)


def pine_sim_config_key(cfg: BotConfig) -> tuple:
    from strategy.ext_range import ext_range_enabled

    return (
        round(float(cfg.wave_min_pct), 8),
        int(cfg.min_opp_bars),
        round(float(cfg.entry_fib_level), 8),
        round(float(cfg.sl_fib_level), 8),
        _abort_fib_cache_part(cfg),
        round(float(cfg.rrr), 8),
        round(float(getattr(cfg, "wave_min_sl", 0.12)), 8),
        bool(getattr(cfg, "wave_plus", False)),
        bool(getattr(cfg, "ext_enabled", False)),
        bool(getattr(cfg, "ext_counter_enabled", False)),
        round(float(getattr(cfg, "ext_wave_min_pct", 0.0) or 0.0), 8),
        bool(getattr(cfg, "trend_hh_hl_filter_enabled", False)),
        bool(ext_range_enabled(cfg)),
        bool(getattr(cfg, "wave_min_pct_enable", False)),
        round(float(getattr(cfg, "ext_post_both_sides_wave_min_pct", 0.0) or 0.0), 8),
        round(float(getattr(cfg, "ext_post_both_sides_default_sl_pct", 0.0) or 0.0), 8),
        round(float(getattr(cfg, "ext_weekend_gap_relax_factor", 0.0) or 0.0), 8),
    )


def _cache_key(df: pd.DataFrame, cfg: BotConfig) -> tuple:
    return (pine_sim_data_key(df), pine_sim_config_key(cfg))


def _deepcopy_result(
    waves: list,
    birth: dict,
    ext_suppress: dict,
    ext_forming: dict,
) -> tuple:
    return (
        copy.deepcopy(waves),
        copy.deepcopy(birth),
        copy.deepcopy(ext_suppress),
        copy.deepcopy(ext_forming),
    )


def get_cached_pine_simulation(
    df: pd.DataFrame,
    cfg: BotConfig,
) -> tuple[list, dict, dict, dict] | None:
    if not _CACHE_ENABLED:
        return None
    key = _cache_key(df, cfg)
    hit = _CACHE.get(key)
    if hit is None:
        return None
    return _deepcopy_result(*hit)


def store_pine_simulation_cache(
    df: pd.DataFrame,
    cfg: BotConfig,
    waves: list,
    birth: dict,
    ext_suppress: dict,
    ext_forming: dict,
) -> None:
    if not _CACHE_ENABLED:
        return
    key = _cache_key(df, cfg)
    _CACHE[key] = _deepcopy_result(waves, birth, ext_suppress, ext_forming)


def run_pine_wave_simulation_cached(df: pd.DataFrame, cfg: BotConfig, **kwargs):
    """Obal run_pine_wave_simulation s per-process cache."""
    cached = get_cached_pine_simulation(df, cfg)
    if cached is not None:
        return cached
    from strategy.wave_detection_pine import run_pine_wave_simulation

    result = run_pine_wave_simulation(df, cfg, **kwargs)
    store_pine_simulation_cache(df, cfg, *result)
    return _deepcopy_result(*result)
