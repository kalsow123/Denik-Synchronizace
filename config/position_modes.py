"""
Rezim pouze klasickych WAVE pozic (fib trend-follow).

Pouziti:
  - grid / vsechny profily: wave_positions_only=True NEBO implicitne (WAVE on, ostatni moduly off)
  - backtest wave study: + wave_isolation_study=True → engine bezi s plnym counterem (stejne WAVE)
  - live: wave_isolation_study=True (B) → engine routing plny, MT5 jen WAVE
"""
from __future__ import annotations

from dataclasses import dataclass, fields, replace
from typing import Any

from config.bot_config import BotConfig


def _grid_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "on")
    return bool(value)


def grid_wave_position_enabled(d: dict) -> bool:
    return _grid_bool(d.get("wave_position_enabled"), True)


def grid_aux_modules_enabled(d: dict) -> bool:
    counter = _grid_bool(d.get("wave_counter_two_sided_enabled"), False)
    if not counter:
        counter = _grid_bool(d.get("counter_position_enabled"), False) or _grid_bool(
            d.get("two_sided_entry_enabled"), False
        )
    if counter:
        return True
    if _grid_bool(d.get("pp_enabled"), False):
        return True
    if _grid_bool(
        d.get("bos_entry_enable"),
        _grid_bool(d.get("bos_reentry_enabled"), False),
    ):
        return True
    if _grid_bool(d.get("bos_entry_in_rrr_fixed"), False):
        return True
    if _grid_bool(d.get("ext_enabled"), False):
        return True
    if _grid_bool(d.get("ext_counter_enabled"), False):
        return True
    if _grid_bool(d.get("ext_secondary_enabled"), False):
        return True
    return False


def grid_is_wave_positions_only(d: dict) -> bool:
    """True = jen klasické WAVE (explicitni flag nebo vsechny pomocne moduly vypnuté)."""
    if _grid_bool(d.get("wave_positions_only"), False):
        return True
    return grid_wave_position_enabled(d) and not grid_aux_modules_enabled(d)


def grid_backtest_isolation_study(d: dict) -> bool:
    return _grid_bool(d.get("wave_isolation_study"), False)


def normalize_legacy_wave_study_combo(combo: dict, profile: dict | None = None) -> None:
    """
    Pri loadu explicit_combos_file: doplni wave study flagy pro legacy JSON bez
    wave_isolation_study (bot_finish study radky s counter off v reportu).
    """
    profile = profile or {}
    if not profile.get("wave_study"):
        return
    if grid_backtest_isolation_study(combo):
        return
    if combo.get("finish_variant") in ("wave_only", "wave_pp"):
        combo["wave_positions_only"] = True
        combo["wave_isolation_study"] = True
        return
    if combo.get("source_combo_no") is not None:
        combo["wave_positions_only"] = True
        combo["wave_isolation_study"] = True
        return
    if not _grid_bool(combo.get("wave_counter_two_sided_enabled"), True):
        combo["wave_positions_only"] = True
        combo["wave_isolation_study"] = True


def bot_config_is_wave_positions_only(cfg: BotConfig) -> bool:
    if bool(getattr(cfg, "wave_positions_only", False)):
        return True
    if not bool(getattr(cfg, "wave_position_enabled", True)):
        return False
    if any(
        (
            bool(getattr(cfg, "wave_counter_two_sided_enabled", False)),
            bool(getattr(cfg, "counter_position_enabled", False)),
            bool(getattr(cfg, "two_sided_entry_enabled", False)),
            bool(getattr(cfg, "pp_enabled", False)),
            bool(getattr(cfg, "bos_entry_enable", False)),
            bool(getattr(cfg, "bos_reentry_enabled", False)),
            bool(getattr(cfg, "bos_entry_in_rrr_fixed", False)),
            bool(getattr(cfg, "ext_enabled", False)),
            bool(getattr(cfg, "ext_counter_enabled", False)),
            bool(getattr(cfg, "ext_secondary_enabled", False)),
        )
    ):
        return False
    return True


@dataclass(frozen=True)
class GridPositionFlagPlan:
    """Vysledek normalizace grid dict → hodnoty pro BotConfig v translatoru."""

    wave_positions_only: bool
    wave_isolation_study: bool
    wave_position_enabled: bool
    wave_counter_two_sided_enabled: bool
    counter_position_enabled: bool
    two_sided_entry_enabled: bool
    pp_enabled: bool
    bos_entry_enable: bool
    bos_entry_in_rrr_fixed: bool
    ext_enabled: bool
    ext_secondary_enabled: bool
    ext_counter_enabled: bool


def plan_grid_position_flags(d: dict) -> GridPositionFlagPlan:
    wave_positions_only = grid_is_wave_positions_only(d)
    wave_isolation_study = grid_backtest_isolation_study(d)

    wave_position_enabled = _grid_bool(d.get("wave_position_enabled"), True)
    if wave_positions_only:
        wave_position_enabled = True

    if "wave_counter_two_sided_enabled" in d:
        wave_counter = _grid_bool(d.get("wave_counter_two_sided_enabled"), False)
    else:
        wave_counter = _grid_bool(d.get("counter_position_enabled"), False) or _grid_bool(
            d.get("two_sided_entry_enabled"), False
        )

    pp_enabled = _grid_bool(d.get("pp_enabled"), False)
    bos_entry_enable = _grid_bool(
        d.get("bos_entry_enable"),
        _grid_bool(d.get("bos_reentry_enabled"), False),
    )
    bos_entry_in_rrr_fixed = _grid_bool(d.get("bos_entry_in_rrr_fixed"), False)
    ext_enabled = _grid_bool(d.get("ext_enabled"), False)
    ext_secondary_enabled = _grid_bool(d.get("ext_secondary_enabled"), False)
    ext_counter_enabled = _grid_bool(d.get("ext_counter_enabled"), False)
    if d.get("ext_bos_enabled") is not None:
        ext_counter_enabled = ext_counter_enabled or _grid_bool(
            d.get("ext_bos_enabled"), False
        )

    if wave_positions_only and not wave_isolation_study:
        wave_counter = False
        pp_enabled = False
        bos_entry_enable = False
        bos_entry_in_rrr_fixed = False
        ext_enabled = False
        ext_secondary_enabled = False
        ext_counter_enabled = False
    elif wave_positions_only and wave_isolation_study:
        # Study: v reportu jen WAVE/counter off; PP/BOS/EXT zustanou ze zdroje.
        wave_counter = False

    # Backtest wave study: plna simulace pro shodne WAVE obchody (jen v engine).
    if wave_isolation_study:
        wave_counter = True

    counter_position_enabled = wave_counter
    two_sided_entry_enabled = wave_counter

    return GridPositionFlagPlan(
        wave_positions_only=wave_positions_only,
        wave_isolation_study=wave_isolation_study,
        wave_position_enabled=wave_position_enabled,
        wave_counter_two_sided_enabled=wave_counter,
        counter_position_enabled=counter_position_enabled,
        two_sided_entry_enabled=two_sided_entry_enabled,
        pp_enabled=pp_enabled,
        bos_entry_enable=bos_entry_enable,
        bos_entry_in_rrr_fixed=bos_entry_in_rrr_fixed,
        ext_enabled=ext_enabled,
        ext_secondary_enabled=ext_secondary_enabled,
        ext_counter_enabled=ext_counter_enabled,
    )


def _bot_config_to_grid_dict(cfg: BotConfig) -> dict:
    """Minimalni dict pro plan_grid_position_flags z BotConfig."""
    return {
        "wave_positions_only": bool(getattr(cfg, "wave_positions_only", False)),
        "wave_isolation_study": bool(getattr(cfg, "wave_isolation_study", False)),
        "wave_position_enabled": bool(getattr(cfg, "wave_position_enabled", True)),
        "wave_counter_two_sided_enabled": bool(
            getattr(cfg, "wave_counter_two_sided_enabled", False)
        ),
        "counter_position_enabled": bool(getattr(cfg, "counter_position_enabled", False)),
        "two_sided_entry_enabled": bool(getattr(cfg, "two_sided_entry_enabled", False)),
        "pp_enabled": bool(getattr(cfg, "pp_enabled", False)),
        "bos_entry_enable": bool(getattr(cfg, "bos_entry_enable", False)),
        "bos_reentry_enabled": bool(getattr(cfg, "bos_reentry_enabled", False)),
        "bos_entry_in_rrr_fixed": bool(getattr(cfg, "bos_entry_in_rrr_fixed", False)),
        "ext_enabled": bool(getattr(cfg, "ext_enabled", False)),
        "ext_secondary_enabled": bool(getattr(cfg, "ext_secondary_enabled", False)),
        "ext_counter_enabled": bool(getattr(cfg, "ext_counter_enabled", False)),
    }


# Pole grid combo neobsahuje — pri merge engine cfg se berou ze zdrojoveho BotConfig.
_LIVE_ONLY_SKIP_GRID_MERGE = frozenset({
    "live_study_two_sided_mirror_orders",
    "live_study_promoted_two_sided_as_wave",
})


def resolve_grid_engine_config(
    cfg: BotConfig,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
) -> BotConfig:
    """
    Engine BotConfig stejně jako grid backtester (combo dict → grid_dict_to_bot_config).

    Combo dict může nést wave_isolation_study=True (report / plan_grid), ale engine
    pole wave_isolation_study zůstane False — counter ordery běží (parita grid combo 2).
    Live-only pole (session, startup_bars, …) se berou ze zdrojového cfg.
    """
    from backtest.grid.translator import bot_config_to_grid_combo_dict, grid_dict_to_bot_config

    combo = bot_config_to_grid_combo_dict(cfg, date_from=date_from, date_to=date_to)
    engine = grid_dict_to_bot_config(combo)
    updates = {
        f.name: getattr(engine, f.name)
        for f in fields(BotConfig)
        if f.name not in _LIVE_ONLY_SKIP_GRID_MERGE
        and getattr(engine, f.name) != getattr(cfg, f.name)
    }
    return replace(cfg, **updates) if updates else cfg


def apply_wave_positions_only_to_bot_config(cfg: BotConfig) -> BotConfig:
    """
    Live / CONFIG_REGISTRY: normalizace WAVE rezimu stejne jako grid translator.

    wave_isolation_study=True (combo 2): zachova ext/counter/bos_entry_in_rrr_fixed
    a zapne engine counter — stejna logika jako plan_grid_position_flags.
    """
    explicit = bool(getattr(cfg, "wave_positions_only", False))
    if not explicit and not bot_config_is_wave_positions_only(cfg):
        return cfg

    d = _bot_config_to_grid_dict(cfg)
    if not explicit:
        d["wave_positions_only"] = True
    plan = plan_grid_position_flags(d)

    names = {f.name for f in fields(BotConfig)}
    updates: dict[str, Any] = {
        "wave_positions_only": plan.wave_positions_only,
        "wave_isolation_study": plan.wave_isolation_study,
        "wave_position_enabled": plan.wave_position_enabled,
        "wave_counter_two_sided_enabled": plan.wave_counter_two_sided_enabled,
        "counter_position_enabled": plan.counter_position_enabled,
        "two_sided_entry_enabled": plan.two_sided_entry_enabled,
        "pp_enabled": plan.pp_enabled,
        "bos_entry_enable": plan.bos_entry_enable,
        "bos_reentry_enabled": plan.bos_entry_enable,
        "bos_entry_in_rrr_fixed": plan.bos_entry_in_rrr_fixed,
        "ext_enabled": plan.ext_enabled,
        "ext_secondary_enabled": plan.ext_secondary_enabled,
        "ext_counter_enabled": plan.ext_counter_enabled,
    }
    return replace(cfg, **{k: v for k, v in updates.items() if k in names})
