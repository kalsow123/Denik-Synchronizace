#!/usr/bin/env python3
"""Backtest vs LIVE E2E pro vybrané combo_no z grid_report.xlsx."""
from __future__ import annotations

import re
import sys
import types
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

COMBO_NOS = [50, 207, 53, 280]
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
    "wave_2_no_tp_enable": True,
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
    ):
        m = re.search(pat, bot_name)
        if m:
            out[key] = cast(m.group(1))
    if "ext_close_trend_positions_on_bosTrue" in bot_name:
        out["ext_close_trend_positions_on_bos"] = True
    elif "ext_close_trend_positions_on_bosFalse" in bot_name:
        out["ext_close_trend_positions_on_bos"] = False
    if "w2notpi2" in bot_name or "wave_2_no_tp_enableTrue" in bot_name:
        out["wave_2_no_tp_enable"] = True
        m = re.search(r"w2notpi(\d+)", bot_name)
        out["wave_2_no_tp_max_index"] = int(m.group(1)) if m else 2
    elif "w2notpFalse" in bot_name or "wave_2_no_tp_enableFalse" in bot_name:
        out["wave_2_no_tp_enable"] = False
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


def run_backtest_wave(combo: dict) -> dict:
    from backtest.engine import BacktestEngine
    from backtest.grid.data_cache import load_data
    from backtest.grid.study_mode import apply_wave_isolation_report_stats, filter_trades_df_for_grid_stats
    from backtest.grid.translator import grid_backtest_position_cap_settings, grid_dict_to_bot_config
    from backtest.sim_params import sim_params_from_grid_combo
    from backtest.stats import compute_stats, trades_to_df

    cfg = grid_dict_to_bot_config(combo)
    cap_mode, cap_limit = grid_backtest_position_cap_settings(combo)
    spr, slip, _ = sim_params_from_grid_combo(combo)
    df = load_data(
        symbol=combo["symbol"],
        timeframe_label=combo["timeframe"],
        date_from=combo["date_from"],
        date_to=combo["date_to"],
    )
    trades = BacktestEngine(
        cfg,
        backtest_position_cap_mode=cap_mode,
        backtest_max_open_positions=cap_limit,
        backtest_spread=spr,
        backtest_slippage=slip,
    ).run(df)
    tdf = filter_trades_df_for_grid_stats(trades_to_df(trades), combo)
    stats = compute_stats(
        tdf,
        date_from=combo["date_from"],
        date_to=combo["date_to"],
    )
    stats = apply_wave_isolation_report_stats(stats, combo)
    ddi = stats.get("ddi_profile", {}) or {}
    return {
        "trades": int(stats.get("total_trades", 0) or 0),
        "net_pnl_usd": round(float(stats.get("net_pnl_usd", 0) or 0), 2),
        "max_drawdown_pct": round(float(stats.get("max_drawdown_pct", 0) or 0), 2),
        "max_ddi_pct": round(float(ddi.get("max_ddi_pct", 0) or 0), 2),
        "p90_ddi_pct": round(float(ddi.get("p90_ddi_pct", 0) or 0), 2),
        "bars": len(df),
    }


def run_live_e2e(combo: dict) -> dict:
    from backtest.data_loader import filter_by_date_range, load_csv
    from backtest.engine import BacktestEngine
    from backtest.grid.data_cache import csv_path_for
    from backtest.stats import classify_position_kind
    from backtest.grid.translator import grid_dict_to_bot_config
    from runtime.live_wave_isolation import resolve_live_execution_config
    from scripts.e2e_live_broker_sim import (
        FakeMt5,
        _clean_wave_time,
        install_fake,
        pnl_ddi_from_closed,
        run_e2e,
    )

    date_from = combo["date_from"]
    date_to = combo["date_to"]
    csv = csv_path_for(combo["symbol"], combo["timeframe"])
    df = filter_by_date_range(load_csv(str(csv)), date_from, date_to).reset_index(drop=True)

    engine_cfg = grid_dict_to_bot_config(combo)
    fake = install_fake(engine_cfg.symbol, engine_cfg.contract_size)

    live_cfg = resolve_live_execution_config(engine_cfg)
    live_cfg.live_study_two_sided_mirror_orders = True
    live_cfg.live_study_promoted_two_sided_as_wave = True

    def bt_wave_pnl(closed):
        return [
            t
            for t in closed
            if classify_position_kind(
                is_pp=bool(getattr(t, "is_pp", 0)),
                is_counter=bool(getattr(t, "is_counter", 0)),
                is_bos_reentry=bool(getattr(t, "is_bos_reentry", 0)),
                is_two_sided_mirror=bool(getattr(t, "is_two_sided_mirror", 0)),
                is_ext=bool(getattr(t, "is_ext", 0)),
                entry_tag=str(getattr(t, "entry_tag", "base")),
            )
            == "WAVE"
        ]

    def live_wave_pnl(closed, *, promoted_waves: set[str] | None = None):
        promoted = promoted_waves or set()
        out = []
        for t in closed:
            wt = _clean_wave_time(getattr(t, "comment", ""))
            is_promoted_ts2 = wt in promoted
            kind = classify_position_kind(
                is_pp=bool(getattr(t, "is_pp", 0)),
                is_counter=bool(getattr(t, "is_counter", 0)),
                is_bos_reentry=bool(getattr(t, "is_bos_reentry", 0)),
                is_two_sided_mirror=bool(getattr(t, "is_two_sided_mirror", 0)) and not is_promoted_ts2,
                is_ext=bool(getattr(t, "is_ext", 0)),
                entry_tag=str(getattr(t, "entry_tag", "base")),
            )
            if kind == "WAVE":
                out.append(t)
            elif kind == "WAVE_TWO_SIDED" and bool(live_cfg.live_study_two_sided_mirror_orders):
                out.append(t)
        return out

    # patch date range in pnl_ddi_from_closed via module globals
    import scripts.e2e_live_broker_sim as e2e_mod

    e2e_mod.DATE_FROM = date_from
    e2e_mod.DATE_TO = date_to

    bt_trades = bt_wave_pnl(
        BacktestEngine(engine_cfg).run(df, retain_wave_snapshot=False)
    )
    bt_stats = pnl_ddi_from_closed(
        [
            types.SimpleNamespace(
                wave_time=t.wave_time,
                dir=t.dir,
                lot=t.lot,
                entry_price=t.entry_price,
                close_price=t.close_price,
                close_reason=t.close_reason,
                pnl_usd=t.pnl_usd,
                is_ext=getattr(t, "is_ext", False),
                is_counter=getattr(t, "is_counter", False),
                is_pp=getattr(t, "is_pp", False),
                is_bos_reentry=getattr(t, "is_bos_reentry", False),
                is_two_sided_mirror=getattr(t, "is_two_sided_mirror", False),
                entry_tag=getattr(t, "entry_tag", "base"),
            )
            for t in bt_trades
        ],
        bot_name=engine_cfg.bot_name,
    )

    live_all = run_e2e(df, live_cfg, fake)
    live_trades = live_wave_pnl(live_all, promoted_waves=fake.promoted_waves)
    lv_stats = pnl_ddi_from_closed(live_trades, bot_name=engine_cfg.bot_name)

    bd = bt_stats.get("ddi_profile", {}) or {}
    ld = lv_stats.get("ddi_profile", {}) or {}
    return {
        "trades": len(live_trades),
        "net_pnl_usd": round(float(lv_stats.get("net_pnl_usd", 0) or 0), 2),
        "max_drawdown_pct": round(float(lv_stats.get("max_drawdown_pct", 0) or 0), 2),
        "max_ddi_pct": round(float(ld.get("max_ddi_pct", 0) or 0), 2),
        "p90_ddi_pct": round(float(ld.get("p90_ddi_pct", 0) or 0), 2),
        "promoted": len(fake.promoted_waves),
        "ts2_count": sum(1 for t in live_all if str(getattr(t, "comment", "")).startswith("TS2_")),
        "bt_e2e_ref_trades": len(bt_trades),
        "bt_e2e_ref_pnl": round(float(bt_stats.get("net_pnl_usd", 0) or 0), 2),
        "bt_e2e_ref_max_ddi": round(float(bd.get("max_ddi_pct", 0) or 0), 2),
    }


def xlsx_ref(row: pd.Series, summaries: pd.DataFrame, ddi: pd.DataFrame) -> dict:
    cn = int(row["combo_no"])
    s = summaries[summaries["combo_no"] == cn]
    d = ddi[ddi["combo_no"] == cn]
    sr = s.iloc[0] if len(s) else None
    dr = d.iloc[0] if len(d) else None
    return {
        "net_pnl_usd": round(float(row.get("net_pnl_usd", 0) or 0), 2),
        "trades_wave": int(row.get("trades_wave", 0) or 0),
        "max_dd_pct": round(float(sr["max_dd_%_vs_initial"]), 2) if sr is not None else None,
        "p90_ddi_pct": round(float(dr["p90_ddi_pct"]), 2) if dr is not None and pd.notna(dr.get("p90_ddi_pct")) else None,
        "median_ddi_pct": round(float(dr["median_ddi_pct"]), 2) if dr is not None and pd.notna(dr.get("median_ddi_pct")) else None,
    }


def main() -> None:
    rows = []
    for rep in REPORTS:
        print("\n" + "=" * 80)
        print("REPORT:", rep["label"], "|", rep["path"].name)
        print("=" * 80)
        vys = pd.read_excel(rep["path"], sheet_name="vysledky")
        summaries = pd.read_excel(rep["path"], sheet_name="summaries")
        ddi = pd.read_excel(rep["path"], sheet_name="ddi_epizody")

        for cn in COMBO_NOS:
            sub = vys[vys["combo_no"] == cn]
            if sub.empty:
                print(f"combo {cn}: NOT FOUND")
                continue
            row = sub.iloc[0]
            combo = combo_from_xlsx_row(row)
            xref = xlsx_ref(row, summaries, ddi)

            print(f"\n--- combo_no {cn} | {combo['date_from']} .. {combo['date_to']} ---")
            print(f"  XLSX ref: PnL={xref['net_pnl_usd']} trades={xref['trades_wave']} maxDD={xref['max_dd_pct']}% p90DDI={xref['p90_ddi_pct']}%")

            print("  Running BACKTEST...")
            bt = run_backtest_wave(combo)
            print(f"  BT rerun:  PnL={bt['net_pnl_usd']} trades={bt['trades']} maxDD={bt['max_drawdown_pct']}% maxDDI={bt['max_ddi_pct']}%")

            print("  Running LIVE E2E...")
            lv = run_live_e2e(combo)
            print(
                f"  LIVE E2E:  PnL={lv['net_pnl_usd']} trades={lv['trades']} maxDD={lv['max_drawdown_pct']}% "
                f"maxDDI={lv['max_ddi_pct']}% promoted={lv['promoted']} TS2={lv['ts2_count']}"
            )
            print(
                f"  Gap: PnL {lv['net_pnl_usd'] - bt['net_pnl_usd']:+.2f} USD | "
                f"DDI {lv['max_ddi_pct'] - bt['max_ddi_pct']:+.2f} pp"
            )

            rows.append(
                {
                    "period": rep["label"],
                    "combo_no": cn,
                    "date_from": combo["date_from"],
                    "date_to": combo["date_to"],
                    "xlsx_pnl_usd": xref["net_pnl_usd"],
                    "xlsx_trades_wave": xref["trades_wave"],
                    "xlsx_max_dd_pct": xref["max_dd_pct"],
                    "xlsx_p90_ddi_pct": xref["p90_ddi_pct"],
                    "bt_pnl_usd": bt["net_pnl_usd"],
                    "bt_trades": bt["trades"],
                    "bt_max_dd_pct": bt["max_drawdown_pct"],
                    "bt_max_ddi_pct": bt["max_ddi_pct"],
                    "bt_p90_ddi_pct": bt["p90_ddi_pct"],
                    "live_pnl_usd": lv["net_pnl_usd"],
                    "live_trades": lv["trades"],
                    "live_max_dd_pct": lv["max_drawdown_pct"],
                    "live_max_ddi_pct": lv["max_ddi_pct"],
                    "live_p90_ddi_pct": lv["p90_ddi_pct"],
                    "live_promoted": lv["promoted"],
                    "live_ts2": lv["ts2_count"],
                    "pnl_gap_live_minus_bt": round(lv["net_pnl_usd"] - bt["net_pnl_usd"], 2),
                    "ddi_gap_live_minus_bt": round(lv["max_ddi_pct"] - bt["max_ddi_pct"], 2),
                }
            )

    out_csv = ROOT / "results/EURUSD/combo_bt_vs_live_e2e_summary.csv"
    out_txt = ROOT / "results/EURUSD/combo_bt_vs_live_e2e_summary.txt"
    df_out = pd.DataFrame(rows)
    df_out.to_csv(out_csv, index=False, sep=";")
    with open(out_txt, "w", encoding="utf-8") as f:
        f.write("COMBO BACKTEST vs LIVE E2E — souhrn\n")
        f.write("Metoda: scripts/grid_combo_bt_vs_live_e2e.py\n")
        f.write("Live E2E: fake MT5 + replay_missed_closed_bar, B+ ON (TS2_ mirrors)\n")
        f.write("Backtest: BacktestEngine WAVE slice (wave_isolation_study)\n\n")
        f.write(df_out.to_string(index=False))
        f.write("\n")
    print("\n" + "=" * 80)
    print("Saved:", out_csv)
    print("Saved:", out_txt)


if __name__ == "__main__":
    main()
