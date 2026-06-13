"""Přednastavené limity prop-firem + načtení custom JSON."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

from backtest.prop_firm.limits import PropFirmLimits

# Limity dle uživatelské specifikace (account 100k pokud není uvedeno jinak).
PROP_FIRM_PRESETS: Dict[str, PropFirmLimits] = {
    "FTMO": PropFirmLimits(
        name="FTMO",
        account_size_usd=100_000.0,
        max_risk_per_moment_pct=None,
        max_risk_single_position_pct=1.0,
        max_daily_dd_pct=5.0,
        max_overall_dd_pct=10.0,
        daily_dd_basis="static_initial",
        profit_target_pct=None,
        min_trading_days=None,
    ),
    "FXIFY": PropFirmLimits(
        name="FXIFY",
        account_size_usd=100_000.0,
        max_risk_per_moment_pct=None,
        max_risk_single_position_pct=None,
        max_daily_dd_pct=4.0,
        max_overall_dd_pct=8.0,
        daily_dd_basis="static_initial",
        profit_target_pct=None,
        min_trading_days=None,
    ),
    "FINTOKEI": PropFirmLimits(
        name="FINTOKEI",
        account_size_usd=100_000.0,
        max_risk_per_moment_pct=3.0,
        max_risk_single_position_pct=None,
        max_daily_dd_pct=5.0,
        max_overall_dd_pct=10.0,
        daily_dd_basis="static_initial",
        profit_target_pct=None,
        min_trading_days=None,
    ),
}

DEFAULT_PROP_FIRM_PRESET = "FTMO"


def load_custom_presets(path: str | Path) -> Dict[str, PropFirmLimits]:
    """JSON: { \"PRESET_KEY\": { \"account_size_usd\": ..., ... } }"""
    from dataclasses import fields

    allowed = {f.name for f in fields(PropFirmLimits)}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    out: Dict[str, PropFirmLimits] = {}
    for key, raw in data.items():
        if not isinstance(raw, dict):
            continue
        kw = {k: v for k, v in raw.items() if k in allowed and k != "name"}
        lim = PropFirmLimits(name=str(raw.get("name", key)), **kw)
        out[str(key)] = lim
    return out


def load_prop_firm_presets(config_path: Optional[str | Path] = None) -> Dict[str, PropFirmLimits]:
    merged = dict(PROP_FIRM_PRESETS)
    if config_path:
        merged.update(load_custom_presets(config_path))
    return merged


def resolve_prop_firm_names(spec: Optional[str]) -> List[str]:
    """
    spec: None → default [FTMO]
          'none' → []
          'all' → všechny presety
          'A,B,C' → seznam klíčů
    """
    if spec is None:
        return [DEFAULT_PROP_FIRM_PRESET]
    s = spec.strip().lower()
    if s in ("", "none"):
        return []
    if s == "all":
        return list(PROP_FIRM_PRESETS.keys())
    return [p.strip() for p in spec.split(",") if p.strip()]
