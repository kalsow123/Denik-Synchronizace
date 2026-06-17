"""Compare DD labels: ALL trades vs WAVE filter vs old sum-loss method."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backtest.engine import BacktestEngine
from backtest.grid.data_cache import load_data
from backtest.grid.study_mode import filter_trades_df_for_grid_stats
from backtest.grid.translator import grid_backtest_position_cap_settings, grid_dict_to_bot_config
from backtest.plotting import _find_drawdown_episodes
from backtest.sim_params import sim_params_from_grid_combo
from backtest.stats import trades_to_df

RUN = ROOT / "results/EURUSD/grid_EXAMPLE_M30_2024-05-10_2024-11-09_001"
INIT = 10_000.0


def pf(v) -> float:
    return float(str(v).replace(",", "."))


def combo_from_row(row: pd.Series) -> dict:
    bn = str(row["bot_name"])
    w2notpi = re.search(r"w2notpi(\d+)", bn)
    return {
        "date_from": str(row["date_from"])[:10],
        "date_to": str(row["date_to"])[:10],
        "timeframe": str(row["timeframe"]),
        "wave_min_pct": pf(row["wave_min_pct"]),
        "min_opp_bars": int(row["min_opp_bars"]),
        "rrr": pf(row["rrr"]),
        "fib_level": pf(row["fib_level"]),
        "entry_mode": str(row["entry_mode"]),
        "symbol": "EURUSD",
        "sl_fib_level": 0.8,
        "abort_fib_level": "shift_sl",
        "wave_plus": True,
        "tp_mode": str(row["tp_mode"]),
        "tp_target_wave_index": int(row["tp_target_wave_index"]),
        "wave_extension_pct": pf(row["wave_extension_pct"]),
        "bos_entry_in_rrr_fixed": bool(row["bos_entry_in_rrr_fixed"]),
        "wave_2_no_tp_enable": "w2notpTrue" in bn,
        "wave_2_no_tp_max_index": int(w2notpi.group(1)) if w2notpi else 2,
        "pending_cancel_mode": str(row["pending_cancel_mode"]),
        "pending_cancel_after_days": int(row["pending_cancel_after_days"]),
        "wave_max_pct": pf(row["wave_max_pct"]),
        "max_wave_age_hours": 20,
        "risk_usd": 500.0,
        "pp_risk_usd": 500.0,
        "contract_size": 100_000.0,
        "magic": 100_001,
        "spread": 0.0001,
        "slippage": 0.0,
        "wave_min_sl": pf(row["wave_min_sl"]),
        "wave_position_enabled": bool(row["wave_position_enabled"]),
        "wave_positions_only": bool(row["wave_positions_only"]),
        "wave_isolation_study": bool(row["wave_isolation_study"]),
        "wave_counter_two_sided_enabled": bool(row["wave_counter_two_sided_enabled"]),
        "two_sided_entry_min_wave_pct": pf(row["two_sided_entry_min_wave_pct"]),
        "skip_primary_entry_on_parent_wave_enable": True,
        "wf_enabled": True,
        "pp_enabled": bool(row["pp_enabled"]),
        "pp_sl_pct": pf(row["pp_sl_pct"]),
        "pp_disabled_in_ext_context": bool(row["pp_disabled_in_ext_context"]),
        "trend_filter_enabled": bool(row["trend_filter_enabled"]),
        "trend_hh_hl_filter_enabled": bool(row["trend_hh_hl_filter_enabled"]),
        "bos_entry_enable": bool(row["bos_entry_enable"]),
        "wave_size_sl_ladder_base_pct": pf(row["wave_size_sl_ladder_base_pct"]),
        "wave_size_sl_ladder_step_pct": pf(row["wave_size_sl_ladder_step_pct"]),
        "wave_size_sl_ladder_band_size_pct": pf(row["wave_size_sl_ladder_band_size_pct"]),
        "ext_enabled": bool(row["ext_enabled"]),
        "ext_wave_min_pct": pf(row["ext_wave_min_pct"]),
        "ext_secondary_enabled": False,
        "ext_weekend_gap_relax_factor": pf(row["ext_weekend_gap_relax_factor"]),
        "ext_counter_enabled": bool(row["ext_counter_enabled"]),
        "ext_counter_time": "23:00",
        "ext_counter_min_sl_enabled": True,
        "ext_counter_min_sl_pct": 0.16,
        "ext_trade_both_sides_in_range": True,
        "wave_min_pct_enable": "wave_min_pct_enableTrue" in bn,
        "ext_post_both_sides_wave_min_pct": 0.35,
        "ext_post_both_sides_default_sl_pct": 0.1,
        "ext_close_trend_positions_on_bos": True,
        "wave_allowed_sessions": None,
        "wave_custom_window": None,
        "track_concurrent_positions": True,
        "backtest_position_cap_mode": "off",
        "backtest_max_open_positions": None,
        "bot_name": bn,
    }


def sum_loss_episodes(times, y_values, pnl_values) -> list[float]:
    """Old bug: sum negative pnl during each underwater episode."""
    t = pd.Series(pd.to_datetime(times)).reset_index(drop=True)
    y = np.asarray(y_values, dtype=float)
    pnl = np.asarray(pnl_values, dtype=float)
    running_peak = -np.inf
    in_ep = False
    loss_sum = 0.0
    out: list[float] = []
    for i in range(len(y)):
        yi = float(y[i])
        if yi >= running_peak - 1e-9:
            if in_ep and loss_sum > 0:
                out.append(loss_sum)
            in_ep = False
            loss_sum = 0.0
            running_peak = yi
        else:
            if not in_ep:
                in_ep = True
                loss_sum = 0.0
            if pnl[i] < 0:
                loss_sum += abs(float(pnl[i]))
    if in_ep and loss_sum > 0:
        out.append(loss_sum)
    return out


def analyze(tdf: pd.DataFrame, label: str) -> None:
    tdf = tdf.sort_values("close_time")
    eq = INIT + tdf["pnl_usd"].astype(float).cumsum()
    eps = _find_drawdown_episodes(tdf["close_time"], eq.values)
    old = sum_loss_episodes(tdf["close_time"], eq.values, tdf["pnl_usd"].values)
    peak = np.maximum.accumulate(np.concatenate([[INIT], eq.values]))[1:]
    global_dd = float((eq.values - peak).min())
    print(f"\n{label}: n={len(tdf)} global_dd={global_dd:.2f}")
    print(f"  peak-trough episodes max: {max((e['loss_usd'] for e in eps), default=0):.0f}")
    print(f"  peak-trough top5: {sorted([e['loss_usd'] for e in eps], reverse=True)[:5]}")
    print(f"  old sum-loss max: {max(old, default=0):.0f}")
    print(f"  old sum-loss top5: {sorted(old, reverse=True)[:5]}")


def main() -> None:
    row = pd.read_csv(RUN / "grid_report.csv", sep=";")
    row = row[row["combo_no"] == 1].iloc[0]
    combo = combo_from_row(row)
    cfg = grid_dict_to_bot_config(combo)
    cap_mode, cap_limit = grid_backtest_position_cap_settings(combo)
    spr, slip, _ = sim_params_from_grid_combo(combo)
    ohlc = load_data("EURUSD", "M30", combo["date_from"], combo["date_to"])
    tdf_all = trades_to_df(
        BacktestEngine(
            cfg,
            backtest_position_cap_mode=cap_mode,
            backtest_max_open_positions=cap_limit,
            backtest_spread=spr,
            backtest_slippage=slip,
        ).run(ohlc)
    )
    tdf_wave = filter_trades_df_for_grid_stats(tdf_all, combo)
    analyze(tdf_all, "ALL trades (no filter)")
    analyze(tdf_wave, "WAVE filter (grid/xlsx)")


if __name__ == "__main__":
    main()
