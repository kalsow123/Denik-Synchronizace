"""Ověření combo_no 2: xlsx vs grid_runner vs plot vs ruční výpočet."""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

from backtest.engine import BacktestEngine
from backtest.grid.backtest_conf import build_grid_bot_name_from_cfg, get_profile
from backtest.grid.data_cache import load_data
from backtest.grid.grid_runner import run_single
from backtest.grid.translator import grid_backtest_position_cap_settings, grid_dict_to_bot_config
from backtest.metrics.ddi_profile import build_daily_ddi_series, compute_ddi_profile
from backtest.plotting import _find_drawdown_episodes
from backtest.sim_params import sim_params_from_grid_combo
from backtest.stats import compute_stats, trades_to_df, _max_dd_pct_vs_initial
from config.position_modes import grid_backtest_isolation_study

RUN_DIR = Path(
    r"results/EURUSD/grid_EXAMPLE_M30_2024-11-10_2025-05-09_001"
)
INIT = 100_000.0
PLOT_INIT = 10_000.0


def _parse_csv_float(s: str) -> float:
    return float(str(s).replace(",", "."))


def combo_from_report_row(row: pd.Series) -> dict:
    """Rekonstrukce grid dict z grid_report.csv řádku (bez wave_2_no_tp — z bot_name)."""
    bn = str(row["bot_name"])
    w2notp = "w2notpTrue" in bn
    w2notpi = re.search(r"w2notpi(\d+)", bn)
    combo = {
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
    }
    combo["_grid_test_pozice"] = int(row["combo_no"])
    combo["bot_name"] = str(row["bot_name"])
    return combo


def manual_peak_trough_usd(tdf: pd.DataFrame, initial: float) -> tuple[float, float]:
    pnl = tdf.sort_values("close_time")["pnl_usd"].astype(float).to_numpy()
    eq = initial + np.cumsum(pnl)
    peak = np.maximum.accumulate(np.concatenate([[initial], eq]))[1:]
    dd = eq - peak
    max_dd_usd = float(dd.min())
    max_dd_pct = round(max_dd_usd / initial * 100, 2)
    return max_dd_usd, max_dd_pct


def main() -> None:
    csv_path = RUN_DIR / "grid_report.csv"
    df_rep = pd.read_csv(csv_path, sep=";")
    row = df_rep[df_rep["combo_no"] == 2].iloc[0]
    combo = combo_from_report_row(row)

    print("=== combo 2 z uloženého grid_report.csv ===")
    print("bot_name match:", combo.get("bot_name") == row["bot_name"])
    print("wave_isolation_study:", combo.get("wave_isolation_study"))
    print("wave_2_no_tp_enable:", combo.get("wave_2_no_tp_enable"))

    xlsx = pd.read_excel(RUN_DIR / "grid_report.xlsx", sheet_name="vysledky")
    xr = xlsx[xlsx["combo_no"] == 2].iloc[0]

    # 1) grid_runner worker (stejná cesta jako při běhu gridu)
    bot_name, stats_worker = run_single(combo)

    # 2) plot re-run (stejná cesta jako plot_top_n_grid)
    cfg = grid_dict_to_bot_config(combo)
    cap_mode, cap_limit = grid_backtest_position_cap_settings(combo)
    spr, slip, _ = sim_params_from_grid_combo(combo)
    ohlc = load_data(
        symbol=combo["symbol"],
        timeframe_label=combo["timeframe"],
        date_from=combo["date_from"],
        date_to=combo["date_to"],
    )
    trades_plot = BacktestEngine(
        cfg,
        backtest_position_cap_mode=cap_mode,
        backtest_max_open_positions=cap_limit,
        backtest_spread=spr,
        backtest_slippage=slip,
    ).run(ohlc)
    tdf_plot = trades_to_df(trades_plot)

    # 3) grid_runner filtr WAVE
    tdf_grid = tdf_plot.copy()
    if grid_backtest_isolation_study(combo) and "position_kind" in tdf_grid.columns:
        tdf_grid = tdf_grid[tdf_grid["position_kind"] == "WAVE"].copy()

    stats_grid = compute_stats(
        tdf_grid,
        initial_balance=INIT,
        date_from=combo["date_from"],
        date_to=combo["date_to"],
    )
    stats_plot_all = compute_stats(
        tdf_plot,
        initial_balance=INIT,
        date_from=combo["date_from"],
        date_to=combo["date_to"],
    )

    # Ruční
    man_grid_usd, man_grid_pct = manual_peak_trough_usd(tdf_grid, INIT)
    man_plot_usd, man_plot_pct = manual_peak_trough_usd(tdf_plot, INIT)
    ddi = compute_ddi_profile(
        tdf_grid,
        initial_balance=INIT,
        date_from=combo["date_from"],
        date_to=combo["date_to"],
    )

    eq_plot = PLOT_INIT + tdf_plot.sort_values("close_time")["pnl_usd"].astype(float).cumsum()
    eps = _find_drawdown_episodes(
        tdf_plot["close_time"],
        eq_plot.values,
        pnl_values=tdf_plot["pnl_usd"].values,
    )
    max_label = max(e["loss_usd"] for e in eps) if eps else 0.0
    peak_trough_plot_10k = float((eq_plot.cummax() - eq_plot).max())

    # HTML label
    html = (RUN_DIR / "grid_all_equity_interactive.html").read_text(encoding="utf-8")
    html_labels = re.findall(r"-\d{1,3}(?:,\d{3})* USD", html)
    html_max = max(int(l.replace("-", "").replace(",", "").replace(" USD", "")) for l in html_labels)

    print("\n=== POČTY OBCHODŮ ===")
    print(f"csv trades / trades_wave: {int(row['trades'])} / {int(row['trades_wave'])}")
    print(f"worker total_trades / trades_wave: {stats_worker.get('total_trades')} / {stats_worker.get('trades_wave')}")
    print(f"re-run ALL: {len(tdf_plot)} | WAVE filter (grid): {len(tdf_grid)}")
    print(f"kind counts ALL: {tdf_plot['position_kind'].value_counts().to_dict() if 'position_kind' in tdf_plot.columns else 'n/a'}")

    print("\n=== MAX DD — xlsx (prop firm používá max_dd_%_vs_initial) ===")
    print(f"xlsx max_dd_usd:              {_parse_csv_float(row['max_dd_usd']):.2f}")
    print(f"xlsx max_dd_%_vs_initial:     {_parse_csv_float(row['max_dd_%_vs_initial']):.2f}")
    print(f"xlsx max_dd_%_vs_initial_wave:{_parse_csv_float(row['max_dd_%_vs_initial_wave']):.2f}")
    print(f"xlsx FTMO peak_overall_dd:    {xr['FTMO__peak_overall_dd_pct']:.2f}")

    print("\n=== MAX DD — grid_runner worker (stejný běh jako grid) ===")
    print(f"worker max_drawdown_usd:      {stats_worker.get('max_drawdown_usd')}")
    print(f"worker max_drawdown_pct:      {stats_worker.get('max_drawdown_pct')}")
    print(f"worker max_drawdown_pct_wave: {stats_worker.get('max_drawdown_pct_wave')}")
    print(f"worker ddi max_ddi_pct:       {stats_worker.get('ddi_profile', {}).get('max_ddi_pct')}")

    print("\n=== MAX DD — ruční peak→trough (initial 100k) ===")
    print(f"WAVE filter:  {man_grid_usd:.2f} USD = {man_grid_pct:.2f} %")
    print(f"ALL trades:   {man_plot_usd:.2f} USD = {man_plot_pct:.2f} %")

    print("\n=== HTML GRAF (NENÍ prop firm metrika) ===")
    print(f"plot initial balance:         {PLOT_INIT:,.0f} USD (report používá {INIT:,.0f})")
    print(f"peak→trough z plot dat:       {peak_trough_plot_10k:.2f} USD")
    print(f"max popisek epizody (sum ztrát): {max_label:,.0f} USD")
    print(f"max popisek v uloženém HTML:    {html_max:,} USD")

    print("\n=== SHODA worker vs xlsx ===")
    w_usd = float(stats_worker.get("max_drawdown_usd", 0))
    x_usd = _parse_csv_float(row["max_dd_usd"])
    print(f"|worker - xlsx| USD: {abs(w_usd - x_usd):.2f}  → {'OK' if abs(w_usd - x_usd) < 1 else 'ROZDÍL'}")
    w_pct = float(stats_worker.get("max_drawdown_pct", 0))
    x_pct = _parse_csv_float(row["max_dd_%_vs_initial"])
    print(f"|worker - xlsx| %:   {abs(w_pct - x_pct):.2f}  → {'OK' if abs(w_pct - x_pct) < 0.05 else 'ROZDÍL'}")


if __name__ == "__main__":
    main()
