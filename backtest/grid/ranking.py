"""grid_ranking.csv — robustness score včetně prop-firm pass count."""
from __future__ import annotations

import math
from typing import Optional

import numpy as np
import pandas as pd


def _normalize_series(s: pd.Series, *, na_fill: float = 0.5) -> pd.Series:
    """0–1 v rámci gridu; chybějící metrika → neutrální na_fill (default 0.5)."""
    s = pd.to_numeric(s, errors="coerce")
    if s.isna().all():
        return pd.Series(na_fill, index=s.index)
    lo, hi = s.min(skipna=True), s.max(skipna=True)
    if hi is None or lo is None or math.isclose(float(hi), float(lo), rel_tol=1e-9, abs_tol=1e-12):
        out = pd.Series(0.5, index=s.index)
    else:
        out = (s - lo) / (hi - lo)
    return out.fillna(na_fill)


def build_grid_ranking(df_report: pd.DataFrame) -> pd.DataFrame:
    """
    Robustness score (součet vah = 1.0):
      0.22 PF + 0.18 Calmar + 0.13 Sortino + 0.13 profitable_months_pct
      + 0.09 inv_loss_streak + 0.10 mar + 0.05 net_pnl + 0.10 prop_firm_pass_count
    """
    if df_report.empty:
        return pd.DataFrame()

    df = df_report.copy()
    pf = _normalize_series(df.get("profit_factor", pd.Series(dtype=float)))
    calmar = _normalize_series(df.get("calmar", pd.Series(dtype=float)))
    sortino = _normalize_series(df.get("sortino", pd.Series(dtype=float)))
    prof_m = _normalize_series(df.get("profitable_months_pct", pd.Series(dtype=float)))

    streak = df.get("longest_loss_streak_trades", pd.Series(dtype=float))
    streak = pd.to_numeric(streak, errors="coerce").replace(0, np.nan)
    inv_streak = _normalize_series(1.0 / streak)

    # mar: menší |max_dd_%| = lepší
    mar_raw = df.get("max_dd_%", df.get("max_dd_%_vs_initial", pd.Series(dtype=float)))
    mar_raw = pd.to_numeric(mar_raw, errors="coerce").abs()
    mar = _normalize_series(-mar_raw)

    net = _normalize_series(df.get("net_pnl_usd", pd.Series(dtype=float)))
    pass_n = _normalize_series(df.get("prop_firm_pass_count", pd.Series(0, index=df.index)))

    score = (
        0.22 * pf
        + 0.18 * calmar
        + 0.13 * sortino
        + 0.13 * prof_m
        + 0.09 * inv_streak
        + 0.10 * mar
        + 0.05 * net
        + 0.10 * pass_n
    )
    df["robustness_score"] = pd.to_numeric(score, errors="coerce").fillna(0.0).round(6)
    cols = [
        "combo_no",
        "bot_name",
        "net_pnl_usd",
        "profit_factor",
        "max_dd_%_vs_initial",
        "calmar",
        "sortino",
        "cagr_pct",
        "prop_firm_pass_count",
        "prop_firm_best_match",
        "robustness_score",
    ]
    keep = [c for c in cols if c in df.columns]
    out = df[keep].sort_values("robustness_score", ascending=False).reset_index(drop=True)
    return out
