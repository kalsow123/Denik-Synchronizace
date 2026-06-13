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
from config.position_modes import apply_wave_positions_only_to_bot_config


def resolve_live_match(config_name: str = "LIVE_BOT_CONFIG") -> List[BotConfig]:
    """
    Vrati JEDEN config z registru. Default: aktualni live config.
    Pouziti: --profile live_match  (nebo --profile live_match --config <NAZEV>)
    """
    if config_name not in CONFIG_REGISTRY:
        raise ValueError(
            f"Neznamy config: '{config_name}'. Dostupne v CONFIG_REGISTRY: "
            f"{list(CONFIG_REGISTRY.keys())}"
        )
    return [apply_wave_positions_only_to_bot_config(CONFIG_REGISTRY[config_name])]


def resolve_compare(config_names: List[str]) -> List[BotConfig]:
    """
    Vrati VICE configu z registru pro porovnani.
    Pouziti: --profile compare --configs LIVE_BOT_CONFIG,EXAMPLE_EURUSD_M15
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
