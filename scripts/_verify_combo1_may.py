"""Diagnostika combo 1: xlsx vs plot (2024-05-10 .. 2024-11-09)."""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

from backtest.engine import BacktestEngine
from backtest.grid.data_cache import load_data
from backtest.grid.grid_runner import run_single
from backtest.grid.study_mode import filter_trades_df_for_grid_stats
from backtest.grid.translator import grid_backtest_position_cap_settings, grid_dict_to_bot_config
from backtest.plotting import _find_drawdown_episodes
from backtest.sim_params import sim_params_from_grid_combo
from backtest.stats import compute_stats, trades_to_df

RUN = Path(r"results/EURUSD/grid_EXAMPLE_M30_2024-05-10_2024-11-09_001")
INIT = 100_000.0
PLOT_INIT = 10_000.0


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
        "_grid_test_pozice": int(row["combo_no"]),
        "bot_name": bn,
    }


def peak_trough(tdf: pd.DataFrame, initial: float) -> tuple[float, float]:
    pnl = tdf.sort_values("close_time")["pnl_usd"].astype(float).to_numpy()
    eq = initial + np.cumsum(pnl)
    peak = np.maximum.accumulate(np.concatenate([[initial], eq]))[1:]
    dd = float((eq - peak).min())
    return dd, round(dd / initial * 100, 2)


def main() -> None:
    row = pd.read_csv(RUN / "grid_report.csv", sep=";")
    row = row[row["combo_no"] == 1].iloc[0]
    combo = combo_from_row(row)
    _, stats = run_single(combo)

    cfg = grid_dict_to_bot_config(combo)
    cap_mode, cap_limit = grid_backtest_position_cap_settings(combo)
    spr, slip, _ = sim_params_from_grid_combo(combo)
    ohlc = load_data(
        symbol="EURUSD",
        timeframe_label="M30",
        date_from=combo["date_from"],
        date_to=combo["date_to"],
    )
    tdf_all = trades_to_df(
        BacktestEngine(
            cfg,
            backtest_position_cap_mode=cap_mode,
            backtest_max_open_positions=cap_limit,
            backtest_spread=spr,
            backtest_slippage=slip,
        ).run(ohlc)
    )
    tdf_plot = filter_trades_df_for_grid_stats(tdf_all, combo)

    for init, name in [(INIT, "100k"), (PLOT_INIT, "10k plot")]:
        usd, pct = peak_trough(tdf_plot, init)
        eq = init + tdf_plot.sort_values("close_time")["pnl_usd"].astype(float).cumsum()
        eps = _find_drawdown_episodes(
            tdf_plot["close_time"], eq.values, pnl_values=tdf_plot["pnl_usd"].values
        )
        max_ep = max(eps, key=lambda e: e["loss_usd"]) if eps else None
        print(f"\n=== {name} | trades={len(tdf_plot)} ===")
        print(f"peak-trough global: {usd:.2f} USD ({pct:.2f}%)")
        if max_ep:
            print(f"max episode label:  {max_ep['loss_usd']:.2f} USD")
        print(f"all episode labels: {[round(e['loss_usd'], 0) for e in sorted(eps, key=lambda x: -x['loss_usd'])[:5]]}")

    print("\n=== xlsx / worker ===")
    print(f"xlsx max_dd_usd:           {pf(row['max_dd_usd']):.2f}")
    print(f"xlsx max_dd_%_vs_initial:  {pf(row['max_dd_%_vs_initial']):.2f}")
    print(f"worker max_drawdown_usd:   {stats.get('max_drawdown_usd')}")
    print(f"worker max_drawdown_pct:   {stats.get('max_drawdown_pct')}")
    print(f"worker ddi max_ddi_pct:    {stats.get('ddi_profile', {}).get('max_ddi_pct')}")

    # HTML labels for combo 1 series
    html = (RUN / "grid_all_equity_interactive.html").read_text(encoding="utf-8")
    bn_short = combo["bot_name"][:80]
    idx = html.find("w2notpTrue_w2notpi2")
    chunk = html[idx : idx + 80000] if idx >= 0 else html
    labels = re.findall(r"-\d{1,3}(?:,\d{3})* USD", chunk)
    print(f"\n=== HTML labels (combo 1 trace) ===")
    print(sorted(set(labels), key=lambda x: -int(x.replace("-", "").replace(",", "").replace(" USD", "")))[:8])

    # Which subplot y-axis is used for DD labels?
    print(f"\nplot uses initial_balance in run_backtest: 10000 (equity offset only; USD DD depth unchanged)")


if __name__ == "__main__":
    main()
