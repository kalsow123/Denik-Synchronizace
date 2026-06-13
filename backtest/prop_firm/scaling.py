"""Výpočet scale_factor pro prop-firma limity (post-processing)."""
from __future__ import annotations

from typing import Any, Optional

import pandas as pd

from backtest.prop_firm.exposure import peak_exposure_at_sl
from backtest.prop_firm.limits import PropFirmLimits

MIN_SCALED_RISK_WARNING_USD = 1.0


def _daily_pnl_series(
    trades_df: pd.DataFrame,
    account_size: float,
    daily_dd_basis: str,
) -> pd.DataFrame:
    """Sloupce: date, daily_pnl_usd, daily_pnl_pct (záporné = ztráta dne)."""
    if trades_df is None or trades_df.empty:
        return pd.DataFrame(columns=["date", "daily_pnl_usd", "daily_pnl_pct"])

    df = trades_df.copy()
    df["close_time"] = pd.to_datetime(df["close_time"])
    df["date"] = df["close_time"].dt.normalize()
    daily = df.groupby("date", sort=True)["pnl_usd"].sum().reset_index()
    daily.columns = ["date", "daily_pnl_usd"]

    if daily_dd_basis == "eod_balance":
        balance = float(account_size)
        pcts = []
        for pnl in daily["daily_pnl_usd"].astype(float):
            denom = balance if balance > 0 else float(account_size)
            pcts.append((float(pnl) / denom) * 100.0 if denom > 0 else 0.0)
            balance += float(pnl)
        daily["daily_pnl_pct"] = pcts
    else:
        init = float(account_size)
        daily["daily_pnl_pct"] = (daily["daily_pnl_usd"].astype(float) / init) * 100.0 if init > 0 else 0.0
    return daily


def _scale_from_peak_pct(limit_pct: Optional[float], peak_pct: float) -> float:
    if limit_pct is None:
        return 1.0
    if peak_pct <= 1e-12:
        return 1.0
    return float(limit_pct) / abs(peak_pct)


def _binding_constraint(scale: float, scales: dict[str, float]) -> str:
    """Který limit vyžaduje snížení risku (final_scale < 1)."""
    if scale >= 1.0 - 1e-12:
        return "none"
    active = {k: v for k, v in scales.items() if v < 1.0 - 1e-12}
    if not active:
        return "none"
    return min(active, key=active.get)


def _binding_headroom_constraint(headroom_scale: float, raw_scales: dict[str, float]) -> str:
    """Nejpřísnější limit pro max. risk (min. z raw scale faktorů; může být > 1)."""
    active = {k: v for k, v in raw_scales.items() if v > 1e-12}
    if not active:
        return "none"
    if headroom_scale >= 1.0 - 1e-12 and all(v >= 1.0 - 1e-12 for v in active.values()):
        return "none"
    return min(active, key=active.get)


def calculate_max_scale_factor(
    trades_df: pd.DataFrame,
    limits: PropFirmLimits,
    *,
    contract_size: float,
    original_net_pnl_usd: float,
    original_max_dd_pct_vs_initial: float,
    original_risk_usd: float,
    peak_overall_dd_pct: Optional[float] = None,
) -> dict[str, Any]:
    """
    Vypočte scale_factor (snížení 0..1) a headroom (násobek risku, může být > 1).

    headroom_scale = min(scale_for_peak, scale_for_single, scale_for_daily, scale_for_overall)
    bez stropu na 1 — určuje max_risk_per_trade_usd a projected_net_pnl_at_max_risk_usd.

    final_scale_factor = min(..., 1) — kolik musíš snížit oproti backtestu, aby historie prošla.
    peak_overall_dd_pct: pokud None, použije |original_max_dd_pct_vs_initial|.
    """
    acct = float(limits.account_size_usd)

    exp = peak_exposure_at_sl(trades_df, contract_size)
    peak_risk_usd = float(exp["peak_risk_usd"])
    peak_single_usd = float(exp.get("peak_single_position_risk_usd", 0.0))
    peak_risk_pct = (peak_risk_usd / acct * 100.0) if acct > 0 else 0.0
    peak_single_pct = (peak_single_usd / acct * 100.0) if acct > 0 else 0.0

    daily = _daily_pnl_series(trades_df, acct, limits.daily_dd_basis)
    if daily.empty:
        worst_day_loss_usd = 0.0
        worst_day_loss_pct = 0.0
        worst_day_date = ""
        trading_days = 0
    else:
        idx_min = daily["daily_pnl_usd"].astype(float).idxmin()
        worst_day_loss_usd = float(daily.loc[idx_min, "daily_pnl_usd"])
        worst_day_loss_pct = float(daily.loc[idx_min, "daily_pnl_pct"])
        worst_day_date = pd.Timestamp(daily.loc[idx_min, "date"]).strftime("%Y-%m-%d")
        trading_days = int(daily["date"].nunique())

    if peak_overall_dd_pct is not None:
        peak_dd_pct = abs(float(peak_overall_dd_pct))
    else:
        peak_dd_pct = abs(float(original_max_dd_pct_vs_initial))

    scale_for_moment = _scale_from_peak_pct(limits.max_risk_per_moment_pct, peak_risk_pct)
    scale_for_single = _scale_from_peak_pct(
        limits.max_risk_single_position_pct, peak_single_pct
    )

    if worst_day_loss_pct < 0 and abs(worst_day_loss_pct) > 1e-12:
        scale_for_daily_dd = limits.max_daily_dd_pct / abs(worst_day_loss_pct)
    else:
        scale_for_daily_dd = 1.0

    if peak_dd_pct > 1e-12:
        scale_for_overall_dd = limits.max_overall_dd_pct / peak_dd_pct
    else:
        scale_for_overall_dd = 1.0

    # Headroom: min jen z limitů, které reálně platí (bez limitu ≠ 1.0, ale vynechat z min).
    headroom_candidates: list[float] = []
    if limits.max_risk_per_moment_pct is not None:
        headroom_candidates.append(scale_for_moment)
    if limits.max_risk_single_position_pct is not None:
        headroom_candidates.append(scale_for_single)
    if worst_day_loss_pct < 0 and abs(worst_day_loss_pct) > 1e-12:
        headroom_candidates.append(scale_for_daily_dd)
    if peak_dd_pct > 1e-12:
        headroom_candidates.append(scale_for_overall_dd)

    headroom_scale = (
        max(0.0, float(min(headroom_candidates))) if headroom_candidates else 1.0
    )

    raw_scales = {
        k: v
        for k, v in {
            "peak_risk": scale_for_moment if limits.max_risk_per_moment_pct is not None else None,
            "single_position_risk": (
                scale_for_single if limits.max_risk_single_position_pct is not None else None
            ),
            "daily_dd": (
                scale_for_daily_dd
                if worst_day_loss_pct < 0 and abs(worst_day_loss_pct) > 1e-12
                else None
            ),
            "overall_dd": scale_for_overall_dd if peak_dd_pct > 1e-12 else None,
        }.items()
        if v is not None
    }
    scale_parts = {k: min(1.0, v) for k, v in raw_scales.items()}
    final_scale = max(0.0, float(min(scale_parts.values())))
    binding = _binding_constraint(final_scale, scale_parts)
    headroom_binding = _binding_headroom_constraint(headroom_scale, raw_scales)

    risk0 = float(original_risk_usd)
    pnl0 = float(original_net_pnl_usd)
    max_risk_trade = risk0 * headroom_scale
    projected_pnl_at_max_risk = pnl0 * headroom_scale
    risk_change_usd = max_risk_trade - risk0

    scaled_worst_day_pct = worst_day_loss_pct * final_scale
    scaled_peak_dd_pct = peak_dd_pct * final_scale

    survives = True
    if final_scale <= 0:
        survives = False
    elif abs(worst_day_loss_pct) > limits.max_daily_dd_pct and abs(scaled_worst_day_pct) > limits.max_daily_dd_pct + 1e-9:
        survives = False
    elif peak_dd_pct > limits.max_overall_dd_pct and scaled_peak_dd_pct > limits.max_overall_dd_pct + 1e-9:
        survives = False

    scaled_net = pnl0 * final_scale
    scaled_net_pct = (scaled_net / acct * 100.0) if acct > 0 else 0.0
    # max_dd_%_vs_initial z backtestu (peak_dd_pct), škálováno lineárně — ne DD od equity peaku.
    scaled_max_dd = -peak_dd_pct * final_scale if peak_dd_pct else 0.0
    scaled_risk_trade = risk0 * final_scale

    profit_target_hit = True
    if limits.profit_target_pct is not None:
        profit_target_hit = scaled_net_pct >= float(limits.profit_target_pct)

    min_days_hit = True
    if limits.min_trading_days is not None:
        min_days_hit = trading_days >= int(limits.min_trading_days)

    challenge_passed = (
        survives
        and profit_target_hit
        and min_days_hit
        and scaled_net > 0
    )

    min_lot_warning = scaled_risk_trade < MIN_SCALED_RISK_WARNING_USD

    return {
        "max_all_positions_risk_usd": peak_risk_usd,
        "max_all_positions_risk_pct": round(peak_risk_pct, 4),
        "max_all_positions_risk_timestamp": exp.get("peak_risk_timestamp", ""),
        "max_risk_per_position_usd": peak_single_usd,
        "max_risk_per_position_pct": round(peak_single_pct, 4),
        "max_risk_per_position_timestamp": exp.get("peak_single_position_risk_timestamp", ""),
        "peak_margin_usd": exp.get("peak_margin_usd", 0.0),
        "peak_margin_timestamp": exp.get("peak_margin_timestamp", ""),
        "worst_day_loss_usd": round(worst_day_loss_usd, 2),
        "worst_day_loss_pct": round(worst_day_loss_pct, 4),
        "worst_day_date": worst_day_date,
        "peak_overall_dd_pct": round(peak_dd_pct, 4),
        "backtest_risk_usd": round(risk0, 2),
        "scale_for_peak_risk": round(scale_for_moment, 6),
        "scale_for_single_position_risk": round(scale_for_single, 6),
        "scale_for_daily_dd": round(scale_for_daily_dd, 6),
        "scale_for_overall_dd": round(scale_for_overall_dd, 6),
        "headroom_scale": round(headroom_scale, 6),
        "headroom_binding": headroom_binding,
        "final_scale_factor": round(final_scale, 6),
        "binding_constraint": binding,
        "prop_firm_survives": survives,
        "original_net_pnl_usd": round(pnl0, 2),
        "scaled_net_pnl_usd": round(scaled_net, 2),
        "scaled_net_pnl_acc_pct": round(scaled_net_pct, 4),
        "scaled_max_dd_pct_vs_initial": round(scaled_max_dd, 4),
        "scaled_risk_per_trade_usd": round(scaled_risk_trade, 2),
        "max_risk_per_trade_usd": round(max_risk_trade, 2),
        "risk_change_usd": round(risk_change_usd, 2),
        "projected_net_pnl_at_max_risk_usd": round(projected_pnl_at_max_risk, 2),
        "min_lot_warning": min_lot_warning,
        "profit_target_hit": profit_target_hit,
        "min_trading_days_hit": min_days_hit,
        "trading_days": trading_days,
        "challenge_passed": challenge_passed,
    }


def trades_records_to_df(records: list) -> pd.DataFrame:
    if not records:
        return pd.DataFrame()
    df = pd.DataFrame(records)
    for col in ("entry_time", "close_time"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col])
    return df
