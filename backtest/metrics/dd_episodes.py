"""Detekce DD epizod >= 10 % vůči initial balance (DDi logika)."""
from __future__ import annotations

import re
from typing import Any

import numpy as np
import pandas as pd

DEFAULT_DD_EPISODE_THRESHOLD_PCT = -10.0
_EPS = 1e-9

_LEGACY_EPISODE_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2})→(\d{4}-\d{2}-\d{2}|OTEVRENO)"
    r"(?:\s*\(([+-]?\d+(?:\.\d+)?)%\))?"
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


def _day_str(ts: Any) -> str:
    return str(pd.Timestamp(ts).normalize().date())


def _calendar_days_between(start: str, end: str) -> int:
    d0 = pd.Timestamp(start).normalize()
    d1 = pd.Timestamp(end).normalize()
    return int((d1 - d0).days)


def find_dd_pct_vs_initial_episodes(
    trades_df: pd.DataFrame,
    initial_balance: float = 100_000.0,
    episode_threshold_pct: float = DEFAULT_DD_EPISODE_THRESHOLD_PCT,
) -> list[dict]:
    """
    Epizody drawdownu na úrovni obchodů; pouze ty s min_dd_pct <= prah (default -10 %).
    """
    df = _prepare_trades(trades_df)
    if df.empty or initial_balance <= 0:
        return []

    init = float(initial_balance)
    pnl = df["pnl_usd"].astype(float).to_numpy()
    run_eq = init + np.cumsum(pnl)
    peak_arr = np.maximum.accumulate(np.concatenate([[init], run_eq]))[1:]
    dd_pct = (run_eq - peak_arr) / init * 100.0
    close_days = [_day_str(t) for t in df["close_time"]]

    episodes: list[dict] = []
    in_ep = False
    start_i = 0
    min_dd_pct = 0.0
    min_i = 0
    n = len(df)

    def _flush(end_i: int, recovery_day: str, is_open: bool) -> None:
        nonlocal in_ep
        if min_dd_pct > episode_threshold_pct + _EPS:
            in_ep = False
            return
        start_day = close_days[start_i]
        trough_day = close_days[min_i]
        end_day = close_days[end_i]
        episodes.append({
            "start_date": start_day,
            "trough_date": trough_day,
            "end_recovery_date": recovery_day,
            "end_date": end_day,
            "min_dd_pct": round(float(min_dd_pct), 2),
            "duration_days": _calendar_days_between(start_day, end_day),
            "days_to_trough": _calendar_days_between(start_day, trough_day),
            "is_open": is_open,
        })
        in_ep = False

    for i in range(n):
        in_drawdown = run_eq[i] < peak_arr[i] - _EPS
        if in_drawdown:
            if not in_ep:
                in_ep = True
                start_i = i
                min_dd_pct = float(dd_pct[i])
                min_i = i
            elif float(dd_pct[i]) < min_dd_pct:
                min_dd_pct = float(dd_pct[i])
                min_i = i
        elif in_ep:
            _flush(i - 1, close_days[i], is_open=False)

    if in_ep:
        _flush(n - 1, "OTEVRENO", is_open=True)

    return episodes


def format_dd_episodes_for_report(episodes: list[dict]) -> str:
    """Legacy text: start→end_recovery (-min_dd_pct%) oddělené |."""
    if not episodes:
        return ""
    parts: list[str] = []
    for ep in episodes:
        end = ep.get("end_recovery_date", "OTEVRENO")
        pct = ep.get("min_dd_pct", 0.0)
        parts.append(f"{ep['start_date']}→{end} ({pct}%)")
    return " | ".join(parts)


def parse_dd_episodes_from_report(text: str) -> list[dict]:
    """Zpětná kompatibilita — parsuje dd_ge_10pct_obdobi text na seznam epizod."""
    if not text or not str(text).strip():
        return []
    episodes: list[dict] = []
    for part in str(text).split("|"):
        part = part.strip()
        if not part:
            continue
        m = _LEGACY_EPISODE_RE.match(part)
        if not m:
            continue
        start_date, end_recovery, pct_str = m.group(1), m.group(2), m.group(3)
        min_dd_pct = round(float(pct_str), 2) if pct_str is not None else 0.0
        is_open = end_recovery == "OTEVRENO"
        end_date = start_date if is_open else end_recovery
        episodes.append({
            "start_date": start_date,
            "trough_date": start_date,
            "end_recovery_date": end_recovery,
            "end_date": end_date,
            "min_dd_pct": min_dd_pct,
            "duration_days": _calendar_days_between(start_date, end_date),
            "days_to_trough": 0,
            "is_open": is_open,
        })
    return episodes


def dd_ge_10pct_obdobi_from_trades(
    trades_df: pd.DataFrame,
    initial_balance: float = 100_000.0,
    episode_threshold_pct: float = DEFAULT_DD_EPISODE_THRESHOLD_PCT,
) -> tuple[list[dict], str]:
    """Vrátí (epizody, legacy text)."""
    episodes = find_dd_pct_vs_initial_episodes(
        trades_df,
        initial_balance=initial_balance,
        episode_threshold_pct=episode_threshold_pct,
    )
    return episodes, format_dd_episodes_for_report(episodes)
