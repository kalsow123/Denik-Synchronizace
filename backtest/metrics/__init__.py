"""Backtest metriky (robustnost, DDi, reportování)."""

from backtest.metrics.dd_episodes import (
    DEFAULT_DD_EPISODE_THRESHOLD_PCT,
    dd_ge_10pct_obdobi_from_trades,
    find_dd_pct_vs_initial_episodes,
    format_dd_episodes_for_report,
    parse_dd_episodes_from_report,
)
from backtest.metrics.ddi_profile import (
    DDI_STAT_COLUMNS,
    build_daily_ddi_series,
    compute_ddi_profile,
)
from backtest.metrics.robustness import (
    ROBUSTNESS_GRID_COLUMNS,
    calculate_cagr,
    calculate_calmar,
    calculate_longest_loss_streak,
    calculate_profitable_months_pct,
    calculate_sortino,
    compute_robustness_metrics,
)

__all__ = [
    "DEFAULT_DD_EPISODE_THRESHOLD_PCT",
    "DDI_STAT_COLUMNS",
    "ROBUSTNESS_GRID_COLUMNS",
    "build_daily_ddi_series",
    "compute_ddi_profile",
    "dd_ge_10pct_obdobi_from_trades",
    "find_dd_pct_vs_initial_episodes",
    "format_dd_episodes_for_report",
    "parse_dd_episodes_from_report",
    "calculate_cagr",
    "calculate_calmar",
    "calculate_longest_loss_streak",
    "calculate_profitable_months_pct",
    "calculate_sortino",
    "compute_robustness_metrics",
]
