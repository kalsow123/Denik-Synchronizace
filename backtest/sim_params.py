"""
Parametry simulace backtestu (spread, slippage, track_concurrent) — nejsou v BotConfig.
Grid: hodnoty z merged combo dictu (base + grid_defaults + větev), viz backtest_conf.PROFILES.
live_match / compare: použijí výchozí konstanty níže (nebo CLI v run_backtest).
"""
from __future__ import annotations

# Výchozí pro --profile live_match a compare (když není grid combo)
DEFAULT_BACKTEST_SPREAD = 0.00002
DEFAULT_BACKTEST_SLIPPAGE = 0.0
DEFAULT_TRACK_CONCURRENT_POSITIONS = False


def sim_params_from_grid_combo(d: dict | None) -> tuple[float, float, bool]:
    """Vrátí (spread, slippage, track_concurrent) z grid dictu nebo defaulty."""
    if not d:
        return (
            DEFAULT_BACKTEST_SPREAD,
            DEFAULT_BACKTEST_SLIPPAGE,
            DEFAULT_TRACK_CONCURRENT_POSITIONS,
        )
    return (
        float(d.get("spread", DEFAULT_BACKTEST_SPREAD)),
        float(d.get("slippage", DEFAULT_BACKTEST_SLIPPAGE)),
        bool(d.get("track_concurrent_positions", DEFAULT_TRACK_CONCURRENT_POSITIONS)),
    )
