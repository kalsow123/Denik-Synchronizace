"""Prop-firma compliance — post-processing grid výsledků (škálování pozic)."""

from backtest.prop_firm.compliance import apply_prop_firm_compliance
from backtest.prop_firm.limits import PropFirmLimits
from backtest.prop_firm.presets import PROP_FIRM_PRESETS, load_prop_firm_presets, resolve_prop_firm_names
from backtest.prop_firm.scaling import calculate_max_scale_factor

__all__ = [
    "PropFirmLimits",
    "PROP_FIRM_PRESETS",
    "apply_prop_firm_compliance",
    "calculate_max_scale_factor",
    "load_prop_firm_presets",
    "resolve_prop_firm_names",
]
