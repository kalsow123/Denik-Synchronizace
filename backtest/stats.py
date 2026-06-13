"""
Vypocet statistik z uzavrenych obchodu.
Vstup: list[ClosedTrade] z BacktestEngine.run()
"""
from __future__ import annotations

import math
from typing import Any, List

import numpy as np
import pandas as pd

from backtest.engine import ClosedTrade
from backtest.metrics.dd_episodes import format_dd_episodes_for_report
from backtest.metrics.ddi_profile import compute_ddi_profile


def classify_position_kind(
    *,
    is_pp: bool,
    is_counter: bool,
    is_bos_reentry: bool,
    is_two_sided_mirror: bool = False,
    is_ext: bool = False,
    entry_tag: str = "base",
) -> str:
    """
    Skupina pro CSV / statistiky:
      PP  — Push-through (is_pp).
      EXT — sekundarni EXT vstup (entry_tag=ext_0236).
      EXT_BOS — EXT counter z EXT bloku (time/BOS).
      WAVE_COUNTER — protipozice LIMIT na TP ceně jen u TP-vln (N, N+2, …).
      BOS — re-entry po BOS flipu mimo EXT.
      WAVE_TWO_SIDED — two-sided mirror (two_sided_entry_enabled).
      WAVE — klasické vstupy z vlny (fib); zbytek po PP/BOS/two-sided/wave_counter.
    """
    tag = str(entry_tag or "base")
    if is_pp:
        return "PP"
    if is_two_sided_mirror:
        return "WAVE_TWO_SIDED"
    if bool(is_ext) and tag == "ext_0236":
        return "EXT"
    if bool(is_ext) and (is_counter or tag in ("ext_counter_time", "ext_counter_bos")):
        return "EXT_BOS"
    if tag == "wave_counter" or is_counter:
        return "WAVE_COUNTER"
    if is_bos_reentry:
        return "BOS"
    return "WAVE"


def _max_dd_pct_vs_initial(df: pd.DataFrame, initial_balance: float) -> float:
    """Max drawdown % vůči initial z equity = initial + kumulativní PnL (řazeno podle close_time)."""
    if df is None or df.empty or initial_balance <= 0:
        return 0.0
    sub = df.sort_values("close_time", kind="mergesort").reset_index(drop=True)
    pnl = sub["pnl_usd"].astype(float).to_numpy()
    init = float(initial_balance)
    run_eq = init + np.cumsum(pnl)
    peak = np.maximum.accumulate(np.concatenate([[init], run_eq]))[1:]
    drawdown = run_eq - peak
    if drawdown.size == 0:
        return 0.0
    max_dd = float(drawdown.min())
    return round((max_dd / init) * 100.0, 2)


def trades_to_df(trades: List[ClosedTrade]) -> pd.DataFrame:
    """Prevede list ClosedTrade na DataFrame pro analyzu."""
    if not trades:
        return pd.DataFrame()
    rows = []
    for t in trades:
        rows.append({
            "wave_time":    t.wave_time,
            "dir":          "BUY" if t.dir == 1 else "SELL",
            "entry_type":   t.entry_type,
            "entry_time":   t.entry_time,
            "close_time":   t.close_time,
            "entry_price":  round(t.entry_price, 5),
            "close_price":  round(t.close_price, 5),
            "sl":           round(t.sl, 5),
            "tp":           None if t.tp is None else round(t.tp, 5),
            "lot":          t.lot,
            "close_reason": t.close_reason,
            "bars_held":    t.bars_held,
            "pnl_usd":      round(t.pnl_usd, 2),
            "is_counter":           bool(getattr(t, "is_counter", False)),
            "is_bos_reentry":       bool(getattr(t, "is_bos_reentry", False)),
            "is_pp":                bool(getattr(t, "is_pp", False)),
            "is_two_sided_mirror":  bool(getattr(t, "is_two_sided_mirror", False)),
            "entry_tag":            str(getattr(t, "entry_tag", "base")),
            "is_ext":               bool(getattr(t, "is_ext", False)),
            "wave_origin":          str(getattr(t, "wave_origin", "normal")),
            "position_kind": classify_position_kind(
                is_pp=bool(getattr(t, "is_pp", False)),
                is_counter=bool(getattr(t, "is_counter", False)),
                is_bos_reentry=bool(getattr(t, "is_bos_reentry", False)),
                is_two_sided_mirror=bool(getattr(t, "is_two_sided_mirror", False)),
                is_ext=bool(getattr(t, "is_ext", False)),
                entry_tag=str(getattr(t, "entry_tag", "base")),
            ),
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df["close_time"] = pd.to_datetime(df["close_time"])
        df = df.sort_values("close_time", kind="mergesort").reset_index(drop=True)
    df["cumulative_pnl"] = df["pnl_usd"].cumsum()
    return df


def compute_concurrent_positions(trades_df: pd.DataFrame) -> dict:
    """
    Spocita maximalni pocet paralelne otevrenych pozic v case.
    Vraci max + druhy nejvyssi level a kolikrat se kazdy vyskytl.
    """
    if trades_df is None or trades_df.empty:
        return {
            "max_concurrent": 0,
            "max_concurrent_count": 0,
            "second_max_concurrent": 0,
            "second_max_concurrent_count": 0,
        }

    events = []
    for _, row in trades_df.iterrows():
        events.append((row["entry_time"], +1))
        events.append((row["close_time"], -1))

    events.sort(key=lambda x: (x[0], x[1]))

    current = 0
    levels: dict = {}
    for _, change in events:
        current += change
        if current > 0:
            levels[current] = levels.get(current, 0) + 1

    if not levels:
        return {
            "max_concurrent": 0,
            "max_concurrent_count": 0,
            "second_max_concurrent": 0,
            "second_max_concurrent_count": 0,
        }

    sorted_levels = sorted(levels.keys(), reverse=True)
    max_val = sorted_levels[0]
    max_count = levels[max_val]
    second_val = sorted_levels[1] if len(sorted_levels) > 1 else 0
    second_count = levels[second_val] if len(sorted_levels) > 1 else 0

    return {
        "max_concurrent": int(max_val),
        "max_concurrent_count": int(max_count),
        "second_max_concurrent": int(second_val),
        "second_max_concurrent_count": int(second_count),
    }


def compute_max_daily_dd_pct(trades_df: pd.DataFrame, initial_balance: float) -> tuple[float, str]:
    """
    Max Daily DD (%) = nejvetsi pokles equity v ramci jednoho kalendarniho dne.
    """
    if trades_df is None or trades_df.empty:
        return 0.0, "N/A"

    df = trades_df.copy()
    df["close_time"] = pd.to_datetime(df["close_time"])
    df = df.sort_values("close_time").reset_index(drop=True)
    df["equity"] = initial_balance + df["pnl_usd"].cumsum()

    max_daily_dd_pct = 0.0
    max_daily_dd_date = "N/A"

    grouped = df.groupby(df["close_time"].dt.date, sort=True)
    for day, day_df in grouped:
        first_idx = day_df.index[0]
        day_start_equity = initial_balance if first_idx == 0 else float(df.loc[first_idx - 1, "equity"])
        day_min_equity = float(day_df["equity"].min())

        if initial_balance > 0:
            dd_pct = (day_min_equity - day_start_equity) / initial_balance * 100.0
            if dd_pct < max_daily_dd_pct:
                max_daily_dd_pct = dd_pct
                max_daily_dd_date = str(day)

    return float(max_daily_dd_pct), max_daily_dd_date


def compute_stats(
    trades_df: pd.DataFrame,
    initial_balance: float = 100_000.0,
    track_concurrent: bool = False,
    date_from: Any | None = None,
    date_to: Any | None = None,
) -> dict:
    """Vypocita zakladni statistiky backtestu."""
    if trades_df.empty:
        return {"error": "Zadne uzavrene obchody."}

    df = trades_df[trades_df["close_reason"] != "END_OF_DATA"].copy()
    if df.empty:
        df = trades_df.copy()

    if "position_kind" not in df.columns:
        if "is_pp" in df.columns:
            _pp = df["is_pp"].astype(bool)
            if "is_counter" in df.columns:
                _ctr = df["is_counter"].astype(bool)
            else:
                _ctr = pd.Series(False, index=df.index)
            if "is_bos_reentry" in df.columns:
                _bre = df["is_bos_reentry"].astype(bool)
            else:
                _bre = pd.Series(False, index=df.index)
            _ext = (
                df["is_ext"].astype(bool)
                if "is_ext" in df.columns else pd.Series(False, index=df.index)
            )
            _tag = (
                df["entry_tag"].astype(str)
                if "entry_tag" in df.columns else pd.Series("base", index=df.index)
            )
            if "is_two_sided_mirror" in df.columns:
                _tsm = df["is_two_sided_mirror"].astype(bool)
            else:
                _tsm = pd.Series(False, index=df.index)
            df["position_kind"] = [
                classify_position_kind(
                    is_pp=a,
                    is_counter=b,
                    is_bos_reentry=c,
                    is_two_sided_mirror=f,
                    is_ext=d,
                    entry_tag=e,
                )
                for a, b, c, f, d, e in zip(_pp, _ctr, _bre, _tsm, _ext, _tag)
            ]
        else:
            df["position_kind"] = "WAVE"

    total_all_closes = len(df)
    pnl = df["pnl_usd"].astype(float)
    _k = df["position_kind"].astype(str)
    wave_mask = _k == "WAVE"
    wave_counter_mask = _k == "WAVE_COUNTER"
    wave_two_sided_mask = _k == "WAVE_TWO_SIDED"
    pp_mask = _k == "PP"
    ext_mask = _k == "EXT"
    bos_mask = _k == "BOS"
    ext_bos_mask = _k == "EXT_BOS"

    trades_wave = int(wave_mask.sum())
    trades_wave_counter = int(wave_counter_mask.sum())
    trades_wave_two_sided = int(wave_two_sided_mask.sum())
    trades_pp = int(pp_mask.sum())
    trades_ext = int(ext_mask.sum())
    trades_bos = int(bos_mask.sum())
    trades_ext_bos = int(ext_bos_mask.sum())

    net_pnl_wave = float(pnl[wave_mask].sum())
    net_pnl_wave_counter = float(pnl[wave_counter_mask].sum())
    net_pnl_wave_two_sided = float(pnl[wave_two_sided_mask].sum())
    net_pnl_pp = float(pnl[pp_mask].sum())
    net_pnl_ext = float(pnl[ext_mask].sum())
    net_pnl_bos = float(pnl[bos_mask].sum())
    net_pnl_ext_bos = float(pnl[ext_bos_mask].sum())
    net_pnl_non_pp = (
        net_pnl_wave
        + net_pnl_wave_counter
        + net_pnl_wave_two_sided
        + net_pnl_ext
        + net_pnl_bos
        + net_pnl_ext_bos
    )
    winning = pnl > 0
    losing = pnl < 0
    total = total_all_closes
    wins = int(winning.sum())
    losses = int(losing.sum())
    win_rate = wins / total if total > 0 else 0.0

    gross_profit = float(pnl[winning].sum())
    gross_loss = float(abs(pnl[losing].sum()))
    net_pnl = float(pnl.sum())
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    avg_win = float(pnl[winning].mean()) if wins > 0 else 0.0
    avg_loss = float(pnl[losing].mean()) if losses > 0 else 0.0

    df_dd = df.sort_values("close_time", kind="mergesort").reset_index(drop=True)
    pnl_dd = df_dd["pnl_usd"].astype(float).to_numpy()
    init = float(initial_balance)
    run_eq = init + np.cumsum(pnl_dd)
    peak_arr = np.maximum.accumulate(np.concatenate([[init], run_eq]))[1:]
    drawdown_arr = run_eq - peak_arr
    max_dd = float(drawdown_arr.min()) if drawdown_arr.size else 0.0
    if drawdown_arr.size:
        max_dd_idx = int(np.argmin(drawdown_arr))
        max_dd_pct_vs_initial = (
            (max_dd / init) * 100 if init > 0 else 0.0
        )
        with np.errstate(divide="ignore", invalid="ignore"):
            dd_pct_vs_peak = np.where(
                peak_arr > 0, (run_eq - peak_arr) / peak_arr * 100.0, 0.0
            )
        max_dd_pct_vs_peak = float(np.nanmin(dd_pct_vs_peak))
        try:
            max_dd_date = str(df_dd["close_time"].iloc[max_dd_idx])[:10]
        except Exception:
            max_dd_date = "N/A"
    else:
        max_dd_pct_vs_initial = 0.0
        max_dd_pct_vs_peak = 0.0
        max_dd_date = "N/A"

    max_dd_pct_wave = _max_dd_pct_vs_initial(
        df[wave_mask], initial_balance
    )
    max_dd_pct_wave_counter = _max_dd_pct_vs_initial(
        df[wave_counter_mask], initial_balance
    )
    max_dd_pct_wave_two_sided = _max_dd_pct_vs_initial(
        df[wave_two_sided_mask], initial_balance
    )
    max_dd_pct_pp = _max_dd_pct_vs_initial(
        df[pp_mask], initial_balance
    )
    max_dd_pct_ext = _max_dd_pct_vs_initial(
        df[ext_mask], initial_balance
    )
    max_dd_pct_bos = _max_dd_pct_vs_initial(
        df[bos_mask], initial_balance
    )
    max_dd_pct_ext_bos = _max_dd_pct_vs_initial(
        df[ext_bos_mask], initial_balance
    )

    try:
        daily_pnl = df.set_index("close_time")["pnl_usd"].resample("D").sum()
        sharpe = (daily_pnl.mean() / daily_pnl.std() * math.sqrt(252)
                  if daily_pnl.std() > 0 else 0.0)
    except Exception:
        sharpe = 0.0

    by_reason = df["close_reason"].value_counts().to_dict()
    max_daily_dd_pct, max_daily_dd_date = compute_max_daily_dd_pct(df, initial_balance)

    stats = {
        "total_closes": int(total_all_closes),
        "total_trades": int(total),
        "trades_wave": trades_wave,
        "trades_wave_counter": trades_wave_counter,
        "trades_wave_two_sided": trades_wave_two_sided,
        "trades_pp": trades_pp,
        "trades_ext": trades_ext,
        "trades_bos": trades_bos,
        "trades_ext_bos": trades_ext_bos,
        "wins": wins,
        "losses": losses,
        "win_rate_pct": round(win_rate * 100, 1),
        "net_pnl_wave_usd": round(net_pnl_wave, 2),
        "net_pnl_wave_counter_usd": round(net_pnl_wave_counter, 2),
        "net_pnl_wave_two_sided_usd": round(net_pnl_wave_two_sided, 2),
        "net_pnl_pp_usd": round(net_pnl_pp, 2),
        "net_pnl_ext_usd": round(net_pnl_ext, 2),
        "net_pnl_bos_usd": round(net_pnl_bos, 2),
        "net_pnl_ext_bos_usd": round(net_pnl_ext_bos, 2),
        "net_pnl_non_pp_usd": round(net_pnl_non_pp, 2),
        "net_pnl_usd": round(float(net_pnl), 2),
        "gross_profit_usd": round(float(gross_profit), 2),
        "gross_loss_usd": round(float(gross_loss), 2),
        "profit_factor": round(float(profit_factor), 2) if profit_factor != float("inf") else "inf",
        "avg_win_usd": round(float(avg_win), 2),
        "avg_loss_usd": round(float(avg_loss), 2),
        "max_drawdown_usd": round(float(max_dd), 2),
        "max_drawdown_pct": round(float(max_dd_pct_vs_initial), 2),
        "max_drawdown_pct_wave": round(float(max_dd_pct_wave), 2),
        "max_drawdown_pct_wave_counter": round(float(max_dd_pct_wave_counter), 2),
        "max_drawdown_pct_wave_two_sided": round(float(max_dd_pct_wave_two_sided), 2),
        "max_drawdown_pct_pp": round(float(max_dd_pct_pp), 2),
        "max_drawdown_pct_ext": round(float(max_dd_pct_ext), 2),
        "max_drawdown_pct_bos": round(float(max_dd_pct_bos), 2),
        "max_drawdown_pct_ext_bos": round(float(max_dd_pct_ext_bos), 2),
        "max_drawdown_pct_vs_peak": round(float(max_dd_pct_vs_peak), 2),
        "sharpe_ratio": round(float(sharpe), 2),
        "max_drawdown_date": max_dd_date,
        "max_daily_dd_pct": round(float(max_daily_dd_pct), 2),
        "max_daily_dd_date": max_daily_dd_date,
        "close_by_reason": by_reason,
    }

    if track_concurrent:
        stats.update(compute_concurrent_positions(trades_df))

    profile = compute_ddi_profile(
        df_dd,
        initial_balance=init,
        date_from=date_from,
        date_to=date_to,
    )
    episodes = profile.pop("episodes", [])
    stats["dd_episodes_ge10pct"] = episodes
    stats["ddi_profile"] = profile
    stats["dd_ge_10pct_obdobi"] = format_dd_episodes_for_report(episodes)

    return stats
