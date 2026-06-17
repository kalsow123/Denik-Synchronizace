"""Porovnání všech combo: xlsx vs grid (WAVE filtr) vs plot (ALL obchody)."""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

from backtest.engine import BacktestEngine
from backtest.grid.grid_runner import run_single
from backtest.grid.translator import grid_backtest_position_cap_settings, grid_dict_to_bot_config
from backtest.grid.data_cache import load_data
from backtest.plotting import _find_drawdown_episodes
from backtest.sim_params import sim_params_from_grid_combo
from backtest.stats import trades_to_df
from config.position_modes import grid_backtest_isolation_study

RUN_DIR = Path(r"results/EURUSD/grid_EXAMPLE_M30_2024-11-10_2025-05-09_001")
INIT = 100_000.0


def _parse_csv_float(s) -> float:
    return float(str(s).replace(",", "."))


def combo_from_report_row(row: pd.Series) -> dict:
    bn = str(row["bot_name"])
    w2notp = "w2notpTrue" in bn
    w2notpi = re.search(r"w2notpi(\d+)", bn)
    return {
        "date_from": str(row["date_from"])[:10],
        "date_to": str(row["date_to"])[:10],
        "timeframe": str(row["timeframe"]),
        "wave_min_pct": _parse_csv_float(row["wave_min_pct"]),
        "min_opp_bars": int(row["min_opp_bars"]),
        "rrr": _parse_csv_float(row["rrr"]),
        "fib_level": _parse_csv_float(row["fib_level"]),
        "entry_mode": str(row["entry_mode"]),
        "symbol": "EURUSD",
        "sl_fib_level": 0.8,
        "abort_fib_level": "shift_sl",
        "wave_plus": True,
        "tp_mode": str(row["tp_mode"]),
        "tp_target_wave_index": int(row["tp_target_wave_index"]),
        "wave_extension_pct": _parse_csv_float(row["wave_extension_pct"]),
        "bos_entry_in_rrr_fixed": bool(row["bos_entry_in_rrr_fixed"]),
        "wave_2_no_tp_enable": w2notp,
        "wave_2_no_tp_max_index": int(w2notpi.group(1)) if w2notpi else 2,
        "pending_cancel_mode": str(row["pending_cancel_mode"]),
        "pending_cancel_after_days": int(row["pending_cancel_after_days"]),
        "wave_max_pct": _parse_csv_float(row["wave_max_pct"]),
        "max_wave_age_hours": 20,
        "risk_usd": 500.0,
        "pp_risk_usd": 500.0,
        "contract_size": 100_000.0,
        "magic": 100_001,
        "spread": 0.0001,
        "slippage": 0.0,
        "wave_min_sl": _parse_csv_float(row["wave_min_sl"]),
        "wave_position_enabled": bool(row["wave_position_enabled"]),
        "wave_positions_only": bool(row["wave_positions_only"]),
        "wave_isolation_study": bool(row["wave_isolation_study"]),
        "wave_counter_two_sided_enabled": bool(row["wave_counter_two_sided_enabled"]),
        "two_sided_entry_min_wave_pct": _parse_csv_float(row["two_sided_entry_min_wave_pct"]),
        "skip_primary_entry_on_parent_wave_enable": True,
        "wf_enabled": True,
        "pp_enabled": bool(row["pp_enabled"]),
        "pp_sl_pct": _parse_csv_float(row["pp_sl_pct"]),
        "pp_disabled_in_ext_context": bool(row["pp_disabled_in_ext_context"]),
        "trend_filter_enabled": bool(row["trend_filter_enabled"]),
        "trend_hh_hl_filter_enabled": bool(row["trend_hh_hl_filter_enabled"]),
        "bos_entry_enable": bool(row["bos_entry_enable"]),
        "wave_size_sl_ladder_base_pct": _parse_csv_float(row["wave_size_sl_ladder_base_pct"]),
        "wave_size_sl_ladder_step_pct": _parse_csv_float(row["wave_size_sl_ladder_step_pct"]),
        "wave_size_sl_ladder_band_size_pct": _parse_csv_float(row["wave_size_sl_ladder_band_size_pct"]),
        "ext_enabled": bool(row["ext_enabled"]),
        "ext_wave_min_pct": _parse_csv_float(row["ext_wave_min_pct"]),
        "ext_secondary_enabled": False,
        "ext_weekend_gap_relax_factor": _parse_csv_float(row["ext_weekend_gap_relax_factor"]),
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


def peak_trough_usd(tdf: pd.DataFrame, initial: float) -> tuple[float, float]:
    if tdf.empty:
        return 0.0, 0.0
    pnl = tdf.sort_values("close_time")["pnl_usd"].astype(float).to_numpy()
    eq = initial + np.cumsum(pnl)
    peak = np.maximum.accumulate(np.concatenate([[initial], eq]))[1:]
    dd = float((eq - peak).min())
    return dd, round(dd / initial * 100, 2)


def max_plot_label_usd(tdf: pd.DataFrame, initial: float) -> float:
    if tdf.empty:
        return 0.0
    eq = initial + tdf.sort_values("close_time")["pnl_usd"].astype(float).cumsum()
    eps = _find_drawdown_episodes(
        tdf["close_time"], eq.values, pnl_values=tdf["pnl_usd"].values
    )
    return max((e["loss_usd"] for e in eps), default=0.0)


def main() -> None:
    df_rep = pd.read_csv(RUN_DIR / "grid_report.csv", sep=";")
    rows = []
    for _, row in df_rep.sort_values("combo_no").iterrows():
        combo = combo_from_report_row(row)
        cn = int(row["combo_no"])
        iso = bool(row["wave_isolation_study"])
        study = str(row.get("study_mode", ""))

        _, stats = run_single(combo)
        xlsx_pct = _parse_csv_float(row["max_dd_%_vs_initial"])
        xlsx_usd = _parse_csv_float(row["max_dd_usd"])
        worker_pct = float(stats.get("max_drawdown_pct", 0))
        worker_usd = float(stats.get("max_drawdown_usd", 0))

        cfg = grid_dict_to_bot_config(combo)
        cap_mode, cap_limit = grid_backtest_position_cap_settings(combo)
        spr, slip, _ = sim_params_from_grid_combo(combo)
        ohlc = load_data(
            symbol=combo["symbol"],
            timeframe_label=combo["timeframe"],
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
        tdf_grid = tdf_all
        if grid_backtest_isolation_study(combo) and "position_kind" in tdf_grid.columns:
            tdf_grid = tdf_grid[tdf_grid["position_kind"] == "WAVE"].copy()

        plot_usd, plot_pct = peak_trough_usd(tdf_all, INIT)
        grid_usd, grid_pct = peak_trough_usd(tdf_grid, INIT)
        label_usd = max_plot_label_usd(tdf_all, INIT)

        non_wave = len(tdf_all) - len(tdf_grid)
        xlsx_worker_ok = abs(xlsx_pct - worker_pct) < 0.05
        plot_vs_xlsx_gap = round(abs(plot_pct) - abs(xlsx_pct), 2)
        plot_matches_grid = abs(plot_pct - grid_pct) < 0.05

        rows.append({
            "combo_no": cn,
            "study_mode": study,
            "wave_isolation": iso,
            "pp": bool(row["pp_enabled"]),
            "counter": bool(row["wave_counter_two_sided_enabled"]),
            "trades_all": len(tdf_all),
            "trades_grid": len(tdf_grid),
            "non_wave_trades": non_wave,
            "xlsx_dd_%": xlsx_pct,
            "worker_dd_%": worker_pct,
            "grid_rerun_dd_%": grid_pct,
            "plot_all_dd_%": plot_pct,
            "xlsx_worker_OK": xlsx_worker_ok,
            "plot_vs_xlsx_gap_%": plot_vs_xlsx_gap,
            "plot_eq_grid": plot_matches_grid,
            "xlsx_dd_usd": xlsx_usd,
            "plot_peak_usd": plot_usd,
            "plot_label_max_usd": label_usd,
        })

    out = pd.DataFrame(rows)
    print(out.to_string(index=False))
    print()
    mismatch = out[~out["plot_eq_grid"]]
    print(f"Plot ALL != grid WAVE (|gap|>0.05%): {len(mismatch)} / {len(out)}")
    if not mismatch.empty:
        print(mismatch[["combo_no", "study_mode", "wave_isolation", "xlsx_dd_%", "plot_all_dd_%", "plot_vs_xlsx_gap_%", "non_wave_trades"]].to_string(index=False))
    iso_m = mismatch[mismatch["wave_isolation"]]
    full_m = mismatch[~mismatch["wave_isolation"]]
    print(f"\nZ toho wave_isolation_study: {len(iso_m)}")
    print(f"Z toho full (ne isolation): {len(full_m)}")


if __name__ == "__main__":
    main()
