"""Regenerate grid_all_equity_interactive.html from existing grid_report.csv."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backtest.plotting import plot_top_n_grid

RUN = ROOT / "results/EURUSD/grid_EXAMPLE_M30_2024-05-10_2024-11-09_001"


def pf(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip()
    if s.lower() in ("true", "false"):
        return s.lower() == "true"
    if "," in s and s.replace(",", ".").replace(".", "", 1).isdigit():
        return float(s.replace(",", "."))
    try:
        if "." not in s and s.lstrip("-").isdigit():
            return int(s)
    except ValueError:
        pass
    return s


def combo_from_row(row: pd.Series) -> dict:
    bn = str(row["bot_name"])
    w2notpi = re.search(r"w2notpi(\d+)", bn)
    return {
        "date_from": str(row["date_from"])[:10],
        "date_to": str(row["date_to"])[:10],
        "timeframe": str(row["timeframe"]),
        "wave_min_pct": pf(row["wave_min_pct"]),
        "min_opp_bars": int(pf(row["min_opp_bars"])),
        "rrr": pf(row["rrr"]),
        "fib_level": pf(row["fib_level"]),
        "entry_mode": str(row["entry_mode"]),
        "symbol": "EURUSD",
        "sl_fib_level": 0.8,
        "abort_fib_level": "shift_sl",
        "wave_plus": True,
        "tp_mode": str(row["tp_mode"]),
        "tp_target_wave_index": int(pf(row["tp_target_wave_index"])),
        "wave_extension_pct": pf(row["wave_extension_pct"]),
        "bos_entry_in_rrr_fixed": bool(row["bos_entry_in_rrr_fixed"]),
        "wave_2_no_tp_enable": "w2notpTrue" in bn,
        "wave_2_no_tp_max_index": int(w2notpi.group(1)) if w2notpi else 2,
        "pending_cancel_mode": str(row["pending_cancel_mode"]),
        "pending_cancel_after_days": int(pf(row["pending_cancel_after_days"])),
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


def main() -> None:
    df = pd.read_csv(RUN / "grid_report.csv", sep=";")
    results = {}
    for _, row in df.iterrows():
        bn = str(row["bot_name"])
        combo = combo_from_row(row)
        results[bn] = {
            "config": combo,
            "net_pnl_usd": pf(row["net_pnl_usd"]),
        }
    out = RUN / "grid_all_equity_interactive.html"
    plot_top_n_grid(
        grid_results=results,
        n=None,
        initial_balance=10_000.0,
        save_path=None,
        interactive_html_path=out,
        show=False,
        preferred_bot_order=df["bot_name"].tolist(),
        primary_prop_preset="FTMO",
        df_report=df,
    )
    html = out.read_text(encoding="utf-8")
    idx = html.find("wave_isolation_studyTrue")
    chunk = html[idx : idx + 120_000] if idx >= 0 else ""
    labels = sorted(
        set(re.findall(r"-\d{1,3}(?:,\d{3})* USD", chunk)),
        key=lambda x: -int(x.replace("-", "").replace(",", "").replace(" USD", "")),
    )
    print(f"Regenerated: {out}")
    print(f"combo1 wave_isolation DD labels (top): {labels[:6]}")


if __name__ == "__main__":
    main()
