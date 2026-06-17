"""
Resolver pro vyber zdroje konfigurace v backtestu.

Tri rezimy:
  - "live_match"  - vezme presne aktualni LIVE_BOT_CONFIG (nebo --config NAZEV)
  - "grid"        - generuje stovky/tisice kombinaci z PROFILES
  - "compare"     - spusti seznam --configs <NAZEV1,NAZEV2,...> vedle sebe

Vraci list[BotConfig], ktery se preda backtest engine.
"""
from __future__ import annotations

from typing import List

from config.bot_config import BotConfig, CONFIG_REGISTRY
from config.position_modes import (
    apply_wave_positions_only_to_bot_config,
    resolve_grid_engine_config,
)


def _registry_config(config_name: str) -> BotConfig:
    if config_name not in CONFIG_REGISTRY:
        raise ValueError(
            f"Neznamy config: '{config_name}'. Dostupne v CONFIG_REGISTRY: "
            f"{list(CONFIG_REGISTRY.keys())}"
        )
    return CONFIG_REGISTRY[config_name]


def resolve_live_match_report_combo(
    config_name: str = "LIVE_BOT_CONFIG",
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    combo_no: int = 1,
) -> dict:
    """
    Combo dict pro stats/report — ze zdrojoveho registru (wave_isolation_study=True).
    Engine config muze mit wave_isolation_study=False (parita grid combo 2).
    """
    from backtest.grid.translator import bot_config_to_grid_combo_dict

    return bot_config_to_grid_combo_dict(
        _registry_config(config_name),
        date_from=date_from,
        date_to=date_to,
        combo_no=combo_no,
    )


def resolve_live_match_pair(
    config_name: str = "LIVE_BOT_CONFIG",
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    combo_no: int = 1,
) -> tuple[BotConfig, dict]:
    """Engine config (grid cesta) + report combo dict (registry metadata)."""
    source = _registry_config(config_name)
    engine = resolve_grid_engine_config(source, date_from=date_from, date_to=date_to)
    combo = resolve_live_match_report_combo(
        config_name,
        date_from=date_from,
        date_to=date_to,
        combo_no=combo_no,
    )
    return engine, combo


def resolve_live_match(config_name: str = "LIVE_BOT_CONFIG") -> List[BotConfig]:
    """
    Vrati JEDEN engine config z registru. Default: aktualni live config.
    Pro report combo pouzij resolve_live_match_report_combo() / resolve_live_match_pair().
    """
    return [resolve_grid_engine_config(_registry_config(config_name))]


def resolve_compare(config_names: List[str]) -> List[BotConfig]:
    """
    Vrati VICE configu z registru pro porovnani.
    Pouziti: --profile compare --configs LIVE_BOT_CONFIG,...
    """
    if not config_names:
        raise ValueError("Pro --profile compare zadej --configs <NAZEV1,NAZEV2,...>")
    configs = []
    for name in config_names:
        name = name.strip()
        if name not in CONFIG_REGISTRY:
            raise ValueError(
                f"Neznamy config: '{name}'. Dostupne v CONFIG_REGISTRY: "
                f"{list(CONFIG_REGISTRY.keys())}"
            )
        configs.append(
            apply_wave_positions_only_to_bot_config(CONFIG_REGISTRY[name])
        )
    return configs


def list_available_configs() -> List[str]:
    """Vraci seznam dostupnych jmen v CONFIG_REGISTRY."""
    return list(CONFIG_REGISTRY.keys())
