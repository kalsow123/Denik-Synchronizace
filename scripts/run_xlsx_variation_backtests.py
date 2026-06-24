#!/usr/bin/env python3
"""
Backtest WAVE slice pro vybrané combo_no z grid_report.xlsx.

NEPOUŽÍVÁ a NEMĚNÍ e2e_live_broker_sim.py.
Live bot = vždy samostatně: scripts/e2e_live_broker_sim.py (LIVE_BOT_CONFIG).

Spuštění:
  .venv\\Scripts\\python.exe scripts/run_xlsx_variation_backtests.py
  .venv\\Scripts\\python.exe scripts/run_xlsx_variation_backtests.py --combo 50 207
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

COMBO_NOS_DEFAULT = [50, 207, 53, 280]
REPORTS = [
    {
        "label": "2025-06-10 .. 2026-06-10",
        "path": ROOT / "results/EURUSD/grid_report 2025-06-10 2026-06-10.xlsx",
    },
    {
        "label": "2024-06-10 .. 2025-07-10",
        "path": ROOT / "results/EURUSD/grid_report_2024-06-10=2025-07-10.xlsx",
    },
]

CONFIG_KEYS = [
    "date_from", "date_to", "timeframe", "wave_min_pct", "rrr", "tp_mode", "tp_target_wave_index",
    "wave_extension_pct", "bos_entry_in_rrr_fixed", "wave_2_no_tp_enable", "wave_2_no_tp_max_index",
    "min_opp_bars", "fib_level", "entry_mode", "symbol", "sl_fib_level", "abort_fib_level", "wave_plus",
    "order_expiry_days", "ext_order_expiry_days", "pending_cancel_mode", "pending_cancel_after_days",
    "wave_max_pct", "max_wave_age_hours", "risk_usd", "pp_risk_usd", "contract_size", "magic", "spread",
    "slippage", "wave_min_sl", "wave_position_enabled", "wave_positions_only", "wave_isolation_study",
    "wave_counter_two_sided_enabled", "two_sided_entry_min_wave_pct", "skip_primary_entry_on_parent_wave_enable",
    "wf_enabled", "pp_enabled", "pp_sl_pct", "pp_disabled_in_ext_context", "trend_filter_enabled",
    "trend_hh_hl_filter_enabled", "bos_entry_enable", "wave_size_sl_ladder_base_pct",
    "wave_size_sl_ladder_step_pct", "wave_size_sl_ladder_band_size_pct", "ext_enabled", "ext_wave_min_pct",
    "ext_secondary_enabled", "ext_weekend_gap_relax_factor", "ext_counter_enabled", "ext_counter_time",
    "ext_counter_min_sl_enabled", "ext_counter_min_sl_pct", "ext_trade_both_sides_in_range",
    "wave_min_pct_enable", "ext_post_both_sides_wave_min_pct", "ext_post_both_sides_default_sl_pct",
    "ext_close_trend_positions_on_bos", "wave_allowed_sessions", "wave_custom_window",
    "track_concurrent_positions", "backtest_position_cap_mode", "backtest_max_open_positions",
]

DEFAULTS = {
    "symbol": "EURUSD",
    "sl_fib_level": 0.8,
    "abort_fib_level": "shift_sl",
    "wave_plus": True,
    "max_wave_age_hours": 20,
    "risk_usd": 500.0,
    "pp_risk_usd": 500.0,
    "contract_size": 100_000.0,
    "magic": 100_001,
    "spread": 0.0001,
    "slippage": 0.0,
    "wave_2_no_tp_enable": False,
    "wave_2_no_tp_max_index": 2,
    "backtest_position_cap_mode": "off",
    "backtest_max_open_positions": None,
    "wave_allowed_sessions": None,
    "wave_custom_window": None,
    "track_concurrent_positions": True,
    "skip_primary_entry_on_parent_wave_enable": True,
    "wf_enabled": True,
    "ext_secondary_enabled": False,
    "ext_trade_both_sides_in_range": True,
    "ext_counter_time": "23:00",
    "ext_counter_min_sl_enabled": True,
    "ext_counter_min_sl_pct": 0.16,
}


def _norm(v, k: str):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, (bool, np.bool_)):
        return bool(v)
    if isinstance(v, pd.Timestamp):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, np.integer):
        return int(v)
    int_keys = {
        "magic", "contract_size", "pending_cancel_after_days", "tp_target_wave_index",
        "wave_2_no_tp_max_index", "min_opp_bars", "order_expiry_days", "ext_order_expiry_days",
    }
    if isinstance(v, (np.floating, float)):
        if k in int_keys and float(v) == int(v):
            return int(v)
        return float(v)
    return v


def _parse_bot_name(bot_name: str) -> dict:
    out: dict = {}
    if "wave_min_pct_enableTrue" in bot_name:
        out["wave_min_pct_enable"] = True
    elif "wave_min_pct_enableFalse" in bot_name:
        out["wave_min_pct_enable"] = False
    for key, pat, cast in (
        ("ext_post_both_sides_wave_min_pct", r"ext_post_both_sides_wave_min_pct([\d.]+)", float),
        ("ext_post_both_sides_default_sl_pct", r"ext_post_both_sides_default_sl_pct([\d.]+)", float),
        ("max_wave_age_hours", r"max_wave_age_hours(\d+)", int),
        ("sl_fib_level", r"_sf([\d.]+)_", float),
    ):
        m = re.search(pat, bot_name)
        if m:
            out[key] = cast(m.group(1))
    if "ext_close_trend_positions_on_bosTrue" in bot_name:
        out["ext_close_trend_positions_on_bos"] = True
    elif "ext_close_trend_positions_on_bosFalse" in bot_name:
        out["ext_close_trend_positions_on_bos"] = False
    # Grid bot_name: w2notpTrue jen kdyz enable=True; w2notpi2 je max_index (muze byt i pri enable=False).
    if "w2notpTrue" in bot_name or "wave_2_no_tp_enableTrue" in bot_name:
        out["wave_2_no_tp_enable"] = True
    elif "w2notpFalse" in bot_name or "wave_2_no_tp_enableFalse" in bot_name:
        out["wave_2_no_tp_enable"] = False
    else:
        out["wave_2_no_tp_enable"] = False
    m = re.search(r"w2notpi(\d+)", bot_name)
    if m:
        out["wave_2_no_tp_max_index"] = int(m.group(1))
    if "ext_counter_enabledTrue" in bot_name or "extct2300" in bot_name:
        out["ext_counter_enabled"] = True
    elif "ext_counter_enabledFalse" in bot_name:
        out["ext_counter_enabled"] = False
    return out


def combo_from_xlsx_row(row: pd.Series) -> dict:
    parsed = _parse_bot_name(str(row["bot_name"]))
    combo: dict = {}
    for k in CONFIG_KEYS:
        if k in row.index and pd.notna(row.get(k)):
            combo[k] = _norm(row[k], k)
        elif k in parsed:
            combo[k] = parsed[k]
        elif k in DEFAULTS:
            combo[k] = DEFAULTS[k]
    if combo.get("wave_min_pct_enable") is None:
        combo["wave_min_pct_enable"] = False
    combo["date_from"] = str(combo["date_from"])[:10]
    combo["date_to"] = str(combo["date_to"])[:10]
    combo["_grid_test_pozice"] = int(row["combo_no"])
    combo["bot_name"] = str(row["bot_name"])
    return combo


def xlsx_reference(row: pd.Series, summaries: pd.DataFrame, ddi: pd.DataFrame) -> dict:
    cn = int(row["combo_no"])
    sr = summaries[summaries["combo_no"] == cn]
    dr = ddi[ddi["combo_no"] == cn]
    return {
        "xlsx_pnl_usd": round(float(row.get("net_pnl_usd", 0) or 0), 2),
        "xlsx_trades_wave": int(row.get("trades_wave", 0) or 0),
        "xlsx_max_dd_pct": round(float(sr.iloc[0]["max_dd_%_vs_initial"]), 2) if len(sr) else None,
        "xlsx_p90_ddi_pct": round(float(dr.iloc[0]["p90_ddi_pct"]), 2) if len(dr) and pd.notna(dr.iloc[0].get("p90_ddi_pct")) else None,
    }


def run_backtest(combo: dict) -> dict:
    from backtest.engine import BacktestEngine
    from backtest.grid.data_cache import load_data
    from backtest.grid.study_mode import apply_wave_isolation_report_stats, filter_trades_df_for_grid_stats
    from backtest.grid.translator import grid_backtest_position_cap_settings, grid_dict_to_bot_config
    from backtest.sim_params import sim_params_from_grid_combo
    from backtest.stats import compute_stats, trades_to_df

    cfg = grid_dict_to_bot_config(combo)
    cap_mode, cap_limit = grid_backtest_position_cap_settings(combo)
    spr, slip, _ = sim_params_from_grid_combo(combo)
    df = load_data(combo["symbol"], combo["timeframe"], combo["date_from"], combo["date_to"])
    trades = BacktestEngine(
        cfg,
        backtest_position_cap_mode=cap_mode,
        backtest_max_open_positions=cap_limit,
        backtest_spread=spr,
        backtest_slippage=slip,
    ).run(df)
    tdf = filter_trades_df_for_grid_stats(trades_to_df(trades), combo)
    stats = apply_wave_isolation_report_stats(
        compute_stats(tdf, date_from=combo["date_from"], date_to=combo["date_to"]),
        combo,
    )
    ddi = stats.get("ddi_profile", {}) or {}
    return {
        "bt_pnl_usd": round(float(stats.get("net_pnl_usd", 0) or 0), 2),
        "bt_trades": int(stats.get("total_trades", 0) or 0),
        "bt_max_dd_pct": round(float(stats.get("max_drawdown_pct", 0) or 0), 2),
        "bt_max_ddi_pct": round(float(ddi.get("max_ddi_pct", 0) or 0), 2),
        "bt_p90_ddi_pct": round(float(ddi.get("p90_ddi_pct", 0) or 0), 2),
        "bars": len(df),
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--combo", type=int, nargs="*", default=COMBO_NOS_DEFAULT)
    args = p.parse_args()

    rows: list[dict] = []
    for rep in REPORTS:
        print("\n" + "=" * 72)
        print(rep["label"], "|", rep["path"].name)
        vys = pd.read_excel(rep["path"], sheet_name="vysledky")
        summaries = pd.read_excel(rep["path"], sheet_name="summaries")
        ddi = pd.read_excel(rep["path"], sheet_name="ddi_epizody")

        for cn in args.combo:
            sub = vys[vys["combo_no"] == cn]
            if sub.empty:
                print(f"  combo {cn}: NOT FOUND")
                continue
            row = sub.iloc[0]
            combo = combo_from_xlsx_row(row)
            xref = xlsx_reference(row, summaries, ddi)
            print(f"\n  combo {cn} | w={combo['wave_min_pct']} fib={combo['fib_level']} sl_fib={combo.get('sl_fib_level')} ext_ctr={combo.get('ext_counter_enabled')}")
            print(f"    XLSX: PnL={xref['xlsx_pnl_usd']} trades={xref['xlsx_trades_wave']} maxDD={xref['xlsx_max_dd_pct']}% p90DDI={xref['xlsx_p90_ddi_pct']}%")
            bt = run_backtest(combo)
            print(
                f"    BT:   PnL={bt['bt_pnl_usd']} trades={bt['bt_trades']} "
                f"maxDD={bt['bt_max_dd_pct']}% maxDDI={bt['bt_max_ddi_pct']}% bars={bt['bars']}"
            )
            print(f"    delta PnL (BT-XLSX): {bt['bt_pnl_usd'] - xref['xlsx_pnl_usd']:+.2f} USD")
            rows.append({"period": rep["label"], "combo_no": cn, **xref, **bt})

    out_dir = ROOT / "results/EURUSD"
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "xlsx_variation_backtest_results.csv"
    txt_path = out_dir / "xlsx_variation_backtest_results.txt"
    df_out = pd.DataFrame(rows)
    df_out.to_csv(csv_path, index=False, sep=";")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("BACKTEST rerun — vybrané combo z grid_report.xlsx (WAVE slice)\n")
        f.write("Live bot: viz COMBO_VARIATIONS_TEST_PLAN.txt → e2e_live_broker_sim.py\n\n")
        f.write(df_out.to_string(index=False))
        f.write("\n")
    print("\nSaved:", csv_path)
    print("Saved:", txt_path)


if __name__ == "__main__":
    main()
