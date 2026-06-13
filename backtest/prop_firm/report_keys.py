"""Sloupce grid_report / výběr TOP kombinací podle projected PnL."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd


def projected_pnl_wide_column(preset: str) -> str:
    return f"{preset}__projected_net_pnl_at_max_risk_usd"


def sort_report_by_projected_pnl(
    df_report: pd.DataFrame,
    preset: str,
    *,
    fallback: str = "net_pnl_usd",
) -> pd.DataFrame:
    col = projected_pnl_wide_column(preset)
    sort_cols = []
    if col in df_report.columns:
        sort_cols.append(col)
    elif fallback in df_report.columns:
        sort_cols.append(fallback)
        
    if not sort_cols:
        return df_report
        
    asc = [False] * len(sort_cols)
    if "study_mode" in df_report.columns:
        sort_cols.append("study_mode")
        asc.append(True)
        
    return df_report.sort_values(sort_cols, ascending=asc, na_position="last").reset_index(drop=True)


def lookup_all_prop_metrics(
    df_long: pd.DataFrame,
    bot_name: str,
    presets: List[str],
) -> Dict[str, Dict[str, Any]]:
    """Metriky všech brokerů/presetů pro scroll HTML (sekce 6)."""
    return {p: lookup_prop_metrics(df_long, bot_name, p) for p in presets if p}


def lookup_prop_metrics(
    df_long: pd.DataFrame,
    bot_name: str,
    preset: str,
) -> Dict[str, Any]:
    """Metriky pro scroll HTML (z listu prop_firm)."""
    empty = {
        "prop_firm_preset": preset,
        "headroom_scale": 1.0,
        "max_risk_per_trade_usd": None,
        "projected_net_pnl_at_max_risk_usd": None,
        "backtest_risk_usd": None,
    }
    if df_long.empty or not preset:
        return empty
    sub = df_long[
        (df_long["prop_firm_name"] == preset) & (df_long["bot_name"] == bot_name)
    ]
    if sub.empty:
        return empty
    row = sub.iloc[0]
    return {
        "prop_firm_preset": preset,
        "headroom_scale": float(row.get("headroom_scale", 1.0) or 1.0),
        "max_risk_per_trade_usd": row.get("max_risk_per_trade_usd"),
        "projected_net_pnl_at_max_risk_usd": row.get("projected_net_pnl_at_max_risk_usd"),
        "backtest_risk_usd": row.get("backtest_risk_usd"),
    }


def scale_trades_df_by_headroom(trades_df: pd.DataFrame, headroom_scale: float) -> pd.DataFrame:
    """Lineární přepočet pnl_usd pro projected scénář (@ max risk)."""
    if trades_df is None or trades_df.empty:
        return trades_df
    out = trades_df.copy()
    h = float(headroom_scale) if headroom_scale is not None else 1.0
    if "pnl_usd" in out.columns:
        out["pnl_usd"] = pd.to_numeric(out["pnl_usd"], errors="coerce").fillna(0.0) * h
    return out
