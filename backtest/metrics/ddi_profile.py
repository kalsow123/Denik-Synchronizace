"""Denní DDi profil a agregované metriky drawdownu vůči initial balance."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from backtest.metrics.dd_episodes import (
    DEFAULT_DD_EPISODE_THRESHOLD_PCT,
    find_dd_pct_vs_initial_episodes,
)

_EPS = 1e-9

DDI_STAT_COLUMNS: tuple[str, ...] = (
    "pocet_epizod_ge10pct",
    "pct_dnu_ge_10",
    "pct_dnu_v_dd",
    "dnu_poruseni_10",
    "dnu_poruseni_5pct",
    "dnu_poruseni_15pct",
    "dnu_poruseni_20pct",
    "breach_streak_max_dnu",
    "prumer_dnu_epizoda_ge10",
    "max_dnu_epizoda_ge10",
    "median_ddi_pct",
    "p90_ddi_pct",
    "pct_dnu_na_peaku_0pct",
    "pct_dnu_ddi_0_az_5pct",
    "pct_dnu_ddi_5_az_10pct",
    "pct_dnu_ddi_10_az_15pct",
    "pct_dnu_ddi_15_az_20pct",
    "pct_dnu_ddi_pod_20pct",
)


def _prepare_trades(trades_df: pd.DataFrame) -> pd.DataFrame:
    if trades_df is None or trades_df.empty:
        return pd.DataFrame()
    df = trades_df.copy()
    if "close_reason" in df.columns:
        filtered = df[df["close_reason"] != "END_OF_DATA"]
        if not filtered.empty:
            df = filtered
    df["close_time"] = pd.to_datetime(df["close_time"])
    return df.sort_values("close_time", kind="mergesort").reset_index(drop=True)


def _normalize_day(ts: Any) -> pd.Timestamp:
    return pd.Timestamp(ts).normalize()


def _in_band(series: pd.Series, low: float | None, high: float | None) -> pd.Series:
    mask = pd.Series(True, index=series.index)
    if high is not None:
        mask &= series <= high + _EPS
    if low is not None:
        mask &= series > low - _EPS
    return mask


def _empty_profile() -> dict:
    return {k: 0 for k in DDI_STAT_COLUMNS} | {"dnu_testu_celkem": 0, "max_ddi_pct": 0.0}


def build_daily_ddi_series(
    trades_df: pd.DataFrame,
    initial_balance: float = 100_000.0,
    date_from: Any | None = None,
    date_to: Any | None = None,
) -> pd.Series:
    """Kalendářní DDi % vůči initial; equity mezi obchody forward-fill."""
    df = _prepare_trades(trades_df)
    init = float(initial_balance)
    if df.empty or init <= 0:
        return pd.Series(dtype=float, name="ddi_pct")

    pnl = df["pnl_usd"].astype(float).to_numpy()
    run_eq = init + np.cumsum(pnl)
    days_idx = pd.to_datetime(df["close_time"]).dt.normalize()
    eq_by_day = pd.Series(run_eq, index=days_idx).groupby(level=0).last()

    if date_from is not None and date_to is not None:
        start = _normalize_day(date_from)
        end = _normalize_day(date_to)
    else:
        start = eq_by_day.index.min()
        end = eq_by_day.index.max()

    cal_days = pd.date_range(start, end, freq="D")
    daily_eq = eq_by_day.reindex(cal_days, method="ffill").fillna(init)
    peak_vals = np.maximum.accumulate(
        np.concatenate([[init], daily_eq.to_numpy(dtype=float)])
    )[1:]
    peak = pd.Series(peak_vals, index=cal_days)
    ddi = (daily_eq - peak) / init * 100.0
    ddi.name = "ddi_pct"
    return ddi


def _breach_streak_max(mask: pd.Series) -> int:
    if mask.empty or not mask.any():
        return 0
    arr = mask.astype(bool).to_numpy()
    best = cur = 0
    for v in arr:
        if v:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return int(best)


def compute_ddi_profile(
    trades_df: pd.DataFrame,
    initial_balance: float = 100_000.0,
    date_from: Any | None = None,
    date_to: Any | None = None,
    episode_threshold_pct: float = DEFAULT_DD_EPISODE_THRESHOLD_PCT,
) -> dict:
    """Agregovaný DDi profil; klíč episodes se do export sheet nepropisuje."""
    ddi = build_daily_ddi_series(
        trades_df,
        initial_balance=initial_balance,
        date_from=date_from,
        date_to=date_to,
    )
    episodes = find_dd_pct_vs_initial_episodes(
        trades_df,
        initial_balance=initial_balance,
        episode_threshold_pct=episode_threshold_pct,
    )

    if ddi.empty:
        profile = _empty_profile()
        profile["episodes"] = episodes
        return profile

    n_days = len(ddi)
    pct = lambda m: round(float(m.sum()) / n_days * 100.0, 2)

    profile: dict[str, Any] = {
        "dnu_testu_celkem": int(n_days),
        "pocet_epizod_ge10pct": len(episodes),
        "pct_dnu_na_peaku_0pct": pct(ddi >= -_EPS),
        "pct_dnu_ddi_0_az_5pct": pct(_in_band(ddi, -5.0, 0)),
        "pct_dnu_ddi_5_az_10pct": pct(_in_band(ddi, -10.0, -5.0)),
        "pct_dnu_ddi_10_az_15pct": pct(_in_band(ddi, -15.0, -10.0)),
        "pct_dnu_ddi_15_az_20pct": pct(_in_band(ddi, -20.0, -15.0)),
        "pct_dnu_ddi_pod_20pct": pct(ddi <= -20.0 + _EPS),
        "pct_dnu_v_dd": pct(ddi < -_EPS),
        "pct_dnu_ge_10": pct(ddi <= -10.0 + _EPS),
        "dnu_poruseni_10": int((ddi <= -10.0 + _EPS).sum()),
        "dnu_poruseni_5pct": int((ddi <= -5.0 + _EPS).sum()),
        "dnu_poruseni_15pct": int((ddi <= -15.0 + _EPS).sum()),
        "dnu_poruseni_20pct": int((ddi <= -20.0 + _EPS).sum()),
        "median_ddi_pct": round(float(ddi.median()), 2),
        "p90_ddi_pct": round(float(np.percentile(ddi.to_numpy(), 10)), 2),
        "max_ddi_pct": round(float(ddi.min()), 2),
        "breach_streak_max_dnu": _breach_streak_max(ddi <= -10.0 + _EPS),
        "prumer_dnu_epizoda_ge10": (
            round(float(np.mean([e["duration_days"] for e in episodes])), 1)
            if episodes else 0.0
        ),
        "max_dnu_epizoda_ge10": (
            int(max(e["duration_days"] for e in episodes)) if episodes else 0
        ),
        "episodes": episodes,
    }
    return profile
