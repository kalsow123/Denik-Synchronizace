"""
Agregace a reportovani vysledku grid backtestu.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import List

import pandas as pd

from backtest.grid.combo_columns import finalize_export_column_order
from backtest.grid.study_mode import (
    apply_wave_isolation_report_stats,
    resolve_study_mode,
    study_base_key,
)
from backtest.io.csv_export import export_csv
from backtest.metrics.robustness import ROBUSTNESS_GRID_COLUMNS, robustness_row_for_grid_report


def build_grid_report(results: dict) -> pd.DataFrame:
    """
    Postavi DataFrame s vysledky vsech kombinaci.
    Razeno sestupne podle net_pnl_usd.
    """

    def _csv_scalar(v):
        if v is None:
            return ""
        if hasattr(v, "value"):
            return str(getattr(v, "value"))
        return v if isinstance(v, (bool, int, float)) else str(v)

    rows = []
    for name, s in results.items():
        if "error" in s:
            continue
        cfg = s.get("config", {})
        s_row = apply_wave_isolation_report_stats(s, cfg)
        rows.append({
            "combo_no":      cfg.get("_grid_test_pozice"),
            "bot_name":      name,
            "date_from":     cfg.get("date_from"),
            "date_to":       cfg.get("date_to"),
            "timeframe":     cfg.get("timeframe"),
            "wave_min_pct":  cfg.get("wave_min_pct"),
            "min_opp_bars":  cfg.get("min_opp_bars"),
            "rrr":           cfg.get("rrr"),
            "fib_level":     cfg.get("fib_level"),
            "entry_mode":    cfg.get("entry_mode"),
            "tp_mode":       _csv_scalar(cfg.get("tp_mode")),
            "pending_cancel_mode": cfg.get("pending_cancel_mode"),
            "pending_cancel_after_days": cfg.get("pending_cancel_after_days"),
            "tp_target_wave_index": cfg.get("tp_target_wave_index"),
            "wave_extension_pct": cfg.get("wave_extension_pct"),
            "wave_counter_two_sided_enabled": cfg.get(
                "wave_counter_two_sided_enabled",
                cfg.get("counter_position_enabled"),
            ),
            "wave_positions_only": cfg.get("wave_positions_only", False),
            "wave_isolation_study": cfg.get("wave_isolation_study", False),
            "study_mode": resolve_study_mode(cfg),
            "bos_entry_enable": cfg.get("bos_entry_enable"),
            "bos_entry_in_rrr_fixed": cfg.get("bos_entry_in_rrr_fixed"),
            "wave_size_sl_ladder_base_pct": cfg.get("wave_size_sl_ladder_base_pct"),
            "wave_size_sl_ladder_step_pct": cfg.get("wave_size_sl_ladder_step_pct"),
            "wave_size_sl_ladder_band_size_pct": cfg.get("wave_size_sl_ladder_band_size_pct"),
            "two_sided_entry_min_wave_pct": cfg.get("two_sided_entry_min_wave_pct"),
            "wave_position_enabled": cfg.get("wave_position_enabled"),
            "wave_min_sl": cfg.get("wave_min_sl"),
            "pp_enabled": cfg.get("pp_enabled"),
            "pp_sl_pct": cfg.get("pp_sl_pct"),
            "pp_risk_usd": cfg.get("pp_risk_usd"),
            "pp_disabled_in_ext_context": cfg.get("pp_disabled_in_ext_context"),
            "ext_enabled": cfg.get("ext_enabled"),
            "ext_wave_min_pct": cfg.get("ext_wave_min_pct"),
            "ext_weekend_gap_relax_factor": cfg.get("ext_weekend_gap_relax_factor"),
            "ext_secondary_fib_level": cfg.get("ext_secondary_fib_level"),
            "ext_counter_enabled": cfg.get("ext_counter_enabled"),
            "ext_counter_time": cfg.get("ext_counter_time"),
            "ext_counter_sl_pct": cfg.get("ext_counter_sl_pct"),
            "ext_counter_min_sl_enabled": cfg.get("ext_counter_min_sl_enabled"),
            "ext_counter_min_sl_pct": cfg.get("ext_counter_min_sl_pct"),
            "ext_bos_fib_level": cfg.get("ext_bos_fib_level"),
            "adx14_change_enabled": cfg.get("adx14_change_enabled"),
            "adx14_equity_gate_enabled": cfg.get("adx14_equity_gate_enabled"),
            "trend_filter_enabled": cfg.get("trend_filter_enabled"),
            "trend_hh_hl_filter_enabled": cfg.get("trend_hh_hl_filter_enabled"),
            "order_expiry_days": cfg.get("order_expiry_days"),
            "ext_order_expiry_days": cfg.get("ext_order_expiry_days"),
            "wave_max_pct":  cfg.get("wave_max_pct"),
            "trades":        s_row.get("total_trades", 0),
            "win_rate_%":    s_row.get("win_rate_pct", 0),
            "trades_wave": s_row.get("trades_wave", 0),
            "net_pnl_wave_usd": s_row.get("net_pnl_wave_usd", 0),
            "max_dd_%_vs_initial_wave": s_row.get("max_drawdown_pct_wave", 0),
            "trades_wave_counter": s_row.get("trades_wave_counter", 0),
            "net_pnl_wave_counter_usd": s_row.get("net_pnl_wave_counter_usd", 0),
            "max_dd_%_vs_initial_wave_counter": s_row.get("max_drawdown_pct_wave_counter", 0),
            "trades_wave_two_sided": s_row.get("trades_wave_two_sided", 0),
            "net_pnl_wave_two_sided_usd": s_row.get("net_pnl_wave_two_sided_usd", 0),
            "max_dd_%_vs_initial_wave_two_sided": s_row.get("max_drawdown_pct_wave_two_sided", 0),
            "trades_pp": s_row.get("trades_pp", 0),
            "net_pnl_pp_usd": s_row.get("net_pnl_pp_usd", 0),
            "max_dd_%_vs_initial_pp": s_row.get("max_drawdown_pct_pp", 0),
            "trades_ext": s_row.get("trades_ext", 0),
            "net_pnl_ext_usd": s_row.get("net_pnl_ext_usd", 0),
            "max_dd_%_vs_initial_ext": s_row.get("max_drawdown_pct_ext", 0),
            "trades_bos": s_row.get("trades_bos", 0),
            "net_pnl_bos_usd": s_row.get("net_pnl_bos_usd", 0),
            "max_dd_%_vs_initial_bos": s_row.get("max_drawdown_pct_bos", 0),
            "trades_ext_bos": s_row.get("trades_ext_bos", 0),
            "net_pnl_ext_bos_usd": s_row.get("net_pnl_ext_bos_usd", 0),
            "max_dd_%_vs_initial_ext_bos": s_row.get("max_drawdown_pct_ext_bos", 0),
            "net_pnl_non_pp_usd": s_row.get("net_pnl_non_pp_usd", 0),
            "net_pnl_usd":   s_row.get("net_pnl_usd", 0),
            "profit_factor": s_row.get("profit_factor", 0),
            "max_dd_usd":    s_row.get("max_drawdown_usd", 0),
            # max_dd_% = od běžícího peaku (shoda s plotting._max_drawdown_pct);
            # max_dd_%_vs_initial = celkem (všechny druhy); _wave / _pp / _bos = dílčí křivky.
            "max_dd_%":      s.get("max_drawdown_pct_vs_peak", s.get("max_drawdown_pct", 0)),
            "max_dd_%_vs_initial": s.get("max_drawdown_pct", 0),
            "max_dd_date":   s.get("max_drawdown_date", "N/A"),
            "max_daily_dd_%": s.get("max_daily_dd_pct", 0),
            "max_daily_dd_date": s.get("max_daily_dd_date", "N/A"),
            "max_pos_open": s.get("max_concurrent", 0),
            "max_pos_open_count": s.get("max_concurrent_count", 0),
            "second_max_pos_open": s.get("second_max_concurrent", 0),
            "second_max_pos_open_count": s.get("second_max_concurrent_count", 0),
            "sharpe":        s.get("sharpe_ratio", 0),
            **robustness_row_for_grid_report(s),
        })
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    if not df.empty:
        full_by_key: dict[tuple, int] = {}
        paired_map: dict[int, int | None] = {}
        for _name, s in results.items():
            if "error" in s:
                continue
            cfg = s.get("config", {})
            cno = cfg.get("_grid_test_pozice")
            if cno is None:
                continue
            mode = resolve_study_mode(cfg)
            key = study_base_key(cfg)
            if mode == "full" and cfg.get("wave_counter_two_sided_enabled"):
                full_by_key.setdefault(key, int(cno))
            elif mode == "wave_isolation":
                paired_map[int(cno)] = None
        for cno in paired_map:
            cfg = next(
                (
                    s.get("config", {})
                    for s in results.values()
                    if "error" not in s
                    and s.get("config", {}).get("_grid_test_pozice") == cno
                ),
                {},
            )
            paired_map[cno] = full_by_key.get(study_base_key(cfg))
        df["paired_full_combo_no"] = df["combo_no"].map(
            lambda x: paired_map.get(int(x)) if pd.notna(x) else None
        )

    df.sort_values(
        ["net_pnl_usd", "study_mode"] if "study_mode" in df.columns else ["net_pnl_usd"], 
        ascending=[False, True] if "study_mode" in df.columns else [False], 
        inplace=True
    )
    df.reset_index(drop=True, inplace=True)
    return finalize_export_column_order(df)


def collect_errors(results: dict) -> pd.DataFrame:
    """Vrati DataFrame s neuspesnymi kombinacemi (pro debugging)."""
    rows = []
    for name, s in results.items():
        if "error" not in s:
            continue
        cfg = s.get("config", {})
        rows.append({
            "combo_no": cfg.get("_grid_test_pozice"),
            "bot_name": name,
            "timeframe": cfg.get("timeframe"),
            "error": s["error"],
        })
    return finalize_export_column_order(pd.DataFrame(rows))


def save_report(df: pd.DataFrame, path) -> None:
    """Ulozi grid report (záloha CSV — hlavní výstup gridu je grid_report.xlsx)."""
    path = str(path)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    export_csv(finalize_export_column_order(df), path, index=False)
    print(f"Report ulozen (CSV zaloha): {path}")


def print_top_n(df: pd.DataFrame, n: int = 10, sort_by: str = "net_pnl_usd") -> None:
    """Vytiskne TOP N kombinaci."""
    if df.empty:
        print("Zadne vysledky k zobrazeni.")
        return
    if sort_by not in df.columns:
        sort_by = "net_pnl_usd"
    top = df.sort_values(sort_by, ascending=False).head(n).copy()
    top.index = range(1, len(top) + 1)
    print(f"\n{'='*100}")
    print(f"  TOP {n} KOMBINACI  -  serazeno podle: {sort_by}")
    print(f"{'='*100}")
    print(top.to_string())
    print(f"{'='*100}\n")


def print_bottom_n(df: pd.DataFrame, n: int = 10, sort_by: str = "net_pnl_usd") -> None:
    """Vytiskne BOTTOM N kombinaci (nejhorsi)."""
    if df.empty:
        print("Zadne vysledky k zobrazeni.")
        return
    if sort_by not in df.columns:
        sort_by = "net_pnl_usd"
    bottom = df.sort_values(sort_by, ascending=True).head(n).copy()
    bottom.index = range(1, len(bottom) + 1)
    print(f"\n{'='*100}")
    print(f"  BOTTOM {n} KOMBINACI  -  serazeno podle: {sort_by}")
    print(f"{'='*100}")
    print(bottom.to_string())
    print(f"{'='*100}\n")


def print_top_n_by_timeframe(df: pd.DataFrame, n: int = 5,
                             sort_by: str = "net_pnl_usd") -> None:
    """Vytiskne TOP N kombinaci pro KAZDY timeframe."""
    if df.empty:
        print("Zadne vysledky k zobrazeni.")
        return
    if sort_by not in df.columns:
        sort_by = "net_pnl_usd"
    for tf in sorted(df["timeframe"].dropna().unique()):
        subset = df[df["timeframe"] == tf].sort_values(sort_by, ascending=False).head(n).copy()
        subset.index = range(1, len(subset) + 1)
        print(f"\n{'='*100}")
        print(f"  TOP {n} - {tf}  |  serazeno podle: {sort_by}")
        print(f"{'='*100}")
        print(subset.to_string())
        print(f"{'='*100}\n")
