"""Souběžná expozice v čase: peak risk (SL) a peak margin (notional)."""
from __future__ import annotations

from typing import List, Tuple

import pandas as pd


def _trade_events(trades_df: pd.DataFrame) -> List[Tuple[pd.Timestamp, int, int]]:
    """(time, delta_open, trade_index) — při stejném čase nejdřív exity."""
    events: List[Tuple[pd.Timestamp, int, int]] = []
    for i, row in trades_df.iterrows():
        et = pd.Timestamp(row["entry_time"])
        ct = pd.Timestamp(row["close_time"])
        events.append((et, +1, int(i)))
        events.append((ct, -1, int(i)))
    events.sort(key=lambda x: (x[0], x[1]))
    return events


def peak_exposure_at_sl(
    trades_df: pd.DataFrame,
    contract_size: float,
) -> dict:
    """
    peak_risk_usd = max součet |entry−sl|*lot*contract_size přes otevřené pozice.
    peak_margin_usd = max součet entry*lot*contract_size (notional / blokovaný kapitál).
    """
    empty = {
        "peak_risk_usd": 0.0,
        "peak_risk_timestamp": "",
        "peak_single_position_risk_usd": 0.0,
        "peak_single_position_risk_timestamp": "",
        "peak_margin_usd": 0.0,
        "peak_margin_timestamp": "",
    }
    if trades_df is None or trades_df.empty or contract_size <= 0:
        return empty

    df = trades_df.copy()
    df["entry_time"] = pd.to_datetime(df["entry_time"])
    df["close_time"] = pd.to_datetime(df["close_time"])
    cs = float(contract_size)

    risks = (df["entry_price"].astype(float) - df["sl"].astype(float)).abs()
    risks = risks * df["lot"].astype(float) * cs
    margins = df["entry_price"].astype(float) * df["lot"].astype(float) * cs
    df = df.assign(_risk_usd=risks, _margin_usd=margins)

    events = _trade_events(df)
    open_idx: set = set()
    peak_risk = 0.0
    peak_risk_ts = ""
    peak_single = 0.0
    peak_single_ts = ""
    peak_margin = 0.0
    peak_margin_ts = ""

    for ts, delta, idx in events:
        if delta > 0:
            open_idx.add(idx)
        else:
            open_idx.discard(idx)
        if not open_idx:
            continue
        open_list = list(open_idx)
        risks = df.loc[open_list, "_risk_usd"].astype(float)
        risk_sum = float(risks.sum())
        max_single = float(risks.max()) if len(risks) else 0.0
        margin_sum = float(df.loc[open_list, "_margin_usd"].sum())
        ts_s = ts.isoformat(sep=" ", timespec="seconds")
        if risk_sum > peak_risk:
            peak_risk = risk_sum
            peak_risk_ts = ts_s
        if max_single > peak_single:
            peak_single = max_single
            peak_single_ts = ts_s
        if margin_sum > peak_margin:
            peak_margin = margin_sum
            peak_margin_ts = ts_s

    return {
        "peak_risk_usd": round(peak_risk, 2),
        "peak_risk_timestamp": peak_risk_ts,
        "peak_single_position_risk_usd": round(peak_single, 2),
        "peak_single_position_risk_timestamp": peak_single_ts,
        "peak_margin_usd": round(peak_margin, 2),
        "peak_margin_timestamp": peak_margin_ts,
    }
