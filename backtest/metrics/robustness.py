"""
Robustnostní metriky pro grid_report.csv (CAGR, Calmar, Sortino, …).
"""
from __future__ import annotations

import logging
import math
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

MIN_TRADES_FOR_METRICS = 10
DEFAULT_INITIAL_BALANCE = 100_000.0
MIN_YEARS_FOR_CAGR = 14 / 365.25  # min. ~2 týdny období (kratší grid okna)
SORTINO_CAP = 99.0  # downside std = 0 (všechny měsíce v zisku)
CALMAR_CAP = 999.0  # max DD = 0 při kladném CAGR

ROBUSTNESS_GRID_COLUMNS = (
    "cagr_pct",
    "calmar",
    "sortino",
    "profitable_months_pct",
    "longest_loss_streak_trades",
    "longest_loss_streak_days",
)


def _nan() -> float:
    return float("nan")


def _round3(value: float) -> float:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return _nan()
    return round(float(value), 3)


def _prepare_trades(trades_df: pd.DataFrame) -> pd.DataFrame:
    if trades_df is None or trades_df.empty:
        return pd.DataFrame()
    out = trades_df[trades_df["close_reason"] != "END_OF_DATA"].copy()
    if out.empty:
        out = trades_df.copy()
    out["close_time"] = pd.to_datetime(out["close_time"])
    out = out.sort_values("close_time", kind="mergesort").reset_index(drop=True)
    return out


def _equity_curve_df(trades_df: pd.DataFrame) -> pd.DataFrame:
    """DataFrame [close_time, cumulative_pnl] seřazený podle close_time."""
    df = _prepare_trades(trades_df)
    if df.empty:
        return pd.DataFrame(columns=["close_time", "cumulative_pnl"])
    return pd.DataFrame(
        {
            "close_time": df["close_time"],
            "cumulative_pnl": df["pnl_usd"].astype(float).cumsum(),
        }
    )


def calculate_cagr(equity_curve_df: pd.DataFrame, initial_balance: float) -> float:
    """
    CAGR v procentech: ((final_equity / initial_balance) ** (1 / years) - 1) * 100.
  """
    if equity_curve_df is None or equity_curve_df.empty:
        return _nan()
    init = float(initial_balance)
    if init <= 0:
        return _nan()

    df = equity_curve_df.sort_values("close_time", kind="mergesort")
    first_t = pd.Timestamp(df["close_time"].iloc[0])
    last_t = pd.Timestamp(df["close_time"].iloc[-1])
    years = (last_t - first_t).days / 365.25
    if years < MIN_YEARS_FOR_CAGR:
        return _nan()

    final_equity = init + float(df["cumulative_pnl"].iloc[-1])
    if final_equity <= 0:
        return _nan()

    cagr = (final_equity / init) ** (1.0 / years) - 1.0
    return float(cagr * 100.0)


def calculate_calmar(cagr_pct: float, max_dd_pct_vs_peak: float) -> float:
    """Calmar = cagr_pct / abs(max_dd_pct_vs_peak). Při DD=0 a CAGR>0 → cap."""
    if cagr_pct is None or max_dd_pct_vs_peak is None:
        return _nan()
    if math.isnan(cagr_pct) or math.isnan(max_dd_pct_vs_peak):
        return _nan()
    dd = abs(float(max_dd_pct_vs_peak))
    if dd < 1e-12:
        return CALMAR_CAP if float(cagr_pct) > 0 else _nan()
    return float(cagr_pct) / dd


def calculate_sortino(
    trades_df: pd.DataFrame,
    risk_free_rate: float = 0.0,
    target_period: str = "monthly",
) -> float:
    """
    Sortino z měsíčních výnosů (součty pnl_usd).
    downside_deviation = std(min(returns - target, 0)); annualizace sqrt(12).
    """
    if target_period != "monthly":
        raise ValueError(f"Nepodporovaný target_period: {target_period!r}")

    df = _prepare_trades(trades_df)
    if df.empty:
        return _nan()

    target = float(risk_free_rate)
    monthly = df.set_index("close_time")["pnl_usd"].astype(float).resample("MS").sum()
    if len(monthly) < 2:
        weekly = df.set_index("close_time")["pnl_usd"].astype(float).resample("W").sum()
        if len(weekly) >= 2:
            monthly = weekly
        else:
            returns = df["pnl_usd"].astype(float).to_numpy()
            if len(returns) < 2:
                return _nan()
            std = float(np.std(returns, ddof=1))
            if std < 1e-12:
                return SORTINO_CAP if float(np.mean(returns)) > target else _nan()
            mean_r = float(np.mean(returns))
            return float(mean_r / std * math.sqrt(252))

    returns = monthly.to_numpy(dtype=float)
    downside = np.minimum(returns - target, 0.0)
    downside_deviation = float(np.std(downside, ddof=1))
    mean_excess = float(np.mean(returns)) - target

    if downside_deviation < 1e-12 or math.isnan(downside_deviation):
        return SORTINO_CAP if mean_excess > 0 else _nan()

    return float(mean_excess / downside_deviation * math.sqrt(12))


def calculate_profitable_months_pct(trades_df: pd.DataFrame) -> float:
    """Procento měsíců s kladným součtem pnl_usd (jen měsíce s ≥1 obchodem)."""
    df = _prepare_trades(trades_df)
    if df.empty:
        return _nan()

    monthly = df.set_index("close_time")["pnl_usd"].astype(float).resample("MS").sum()
    if len(monthly) == 0:
        return _nan()

    profitable = int((monthly > 0).sum())
    total = len(monthly)
    return float(profitable / total * 100.0)


def calculate_longest_loss_streak(
    trades_df: pd.DataFrame,
    *,
    initial_balance: float = DEFAULT_INITIAL_BALANCE,
) -> dict[str, float]:
    """
    trades: nejdelší souvislá série ztrátových obchodů (pnl_usd < 0).
    days: nejdelší doba v drawdownu od peaku equity do obnovy (kalendářní dny).
    """
    df = _prepare_trades(trades_df)
    if df.empty:
        return {"trades": _nan(), "days": _nan()}

    pnl = df["pnl_usd"].astype(float)
    max_streak = 0
    streak = 0
    for v in pnl:
        if v < 0:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0

    init = float(initial_balance)
    equity = init + pnl.cumsum()
    times = df["close_time"]

    peak_equity = init
    peak_time = pd.Timestamp(times.iloc[0])
    max_dd_days = 0
    in_drawdown = False
    dd_start = peak_time

    for i in range(len(df)):
        eq = float(equity.iloc[i])
        t = pd.Timestamp(times.iloc[i])
        if eq >= peak_equity:
            if in_drawdown:
                duration = (t - dd_start).days
                max_dd_days = max(max_dd_days, duration)
            peak_equity = eq
            peak_time = t
            in_drawdown = False
        else:
            if not in_drawdown:
                in_drawdown = True
                dd_start = peak_time

    if in_drawdown:
        duration = (pd.Timestamp(times.iloc[-1]) - dd_start).days
        max_dd_days = max(max_dd_days, duration)

    return {"trades": float(max_streak), "days": float(max_dd_days)}


def _empty_robustness_row() -> dict[str, float]:
    return {k: _nan() for k in ROBUSTNESS_GRID_COLUMNS}


def compute_robustness_metrics(
    trades_df: pd.DataFrame,
    *,
    initial_balance: float = DEFAULT_INITIAL_BALANCE,
    max_dd_pct_vs_peak: float | None = None,
    max_dd_pct_vs_initial: float | None = None,
    bot_name: str = "",
) -> dict[str, float]:
    """
    Vypočte všechny robustnostní metriky pro grid_report (zaokrouhlené na 3 des. místa).
    Při < MIN_TRADES_FOR_METRICS obchodů vrátí NaN.
    """
    df = _prepare_trades(trades_df)
    if len(df) < MIN_TRADES_FOR_METRICS:
        return _empty_robustness_row()

    try:
        eq_df = _equity_curve_df(trades_df)
        cagr = calculate_cagr(eq_df, initial_balance)
        dd_for_calmar = max_dd_pct_vs_peak
        if dd_for_calmar is None or (
            isinstance(dd_for_calmar, float) and math.isnan(dd_for_calmar)
        ):
            dd_for_calmar = _nan()
        elif abs(float(dd_for_calmar)) < 1e-12 and max_dd_pct_vs_initial is not None:
            dd_for_calmar = max_dd_pct_vs_initial
        calmar = calculate_calmar(cagr, dd_for_calmar)
        sortino = calculate_sortino(trades_df)
        prof_months = calculate_profitable_months_pct(trades_df)
        streak = calculate_longest_loss_streak(trades_df, initial_balance=initial_balance)

        return {
            "cagr_pct": _round3(cagr),
            "calmar": _round3(calmar),
            "sortino": _round3(sortino),
            "profitable_months_pct": _round3(prof_months),
            "longest_loss_streak_trades": _round3(streak["trades"]),
            "longest_loss_streak_days": _round3(streak["days"]),
        }
    except Exception as exc:
        label = bot_name or "?"
        logger.warning(
            "Robustnostní metriky selhaly pro %s: %s",
            label,
            exc,
            exc_info=True,
        )
        print(f"[robustness] VAROVANI: metriky pro {label}: {exc}")
        return _empty_robustness_row()


def robustness_row_for_grid_report(stats: dict) -> dict[str, Any]:
    """Mapuje klíče ze stats dict na sloupce grid_report (už vypočtené v grid_runner)."""
    return {col: stats.get(col, _nan()) for col in ROBUSTNESS_GRID_COLUMNS}
