"""Generate EXAMPLE grid blocks from grid_report.xlsx (one-off helper)."""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

CONFIG_KEYS = [
    "date_from", "date_to", "timeframe", "causal_mode", "run_e2e_parity", "wave_min_pct", "rrr",
    "tp_mode", "tp_target_wave_index", "wave_extension_pct", "bos_entry_in_rrr_fixed",
    "wave_2_no_tp_enable", "wave_2_no_tp_max_index", "min_opp_bars", "fib_level", "entry_mode",
    "symbol", "sl_fib_level", "abort_fib_level", "wave_plus", "order_expiry_days",
    "ext_order_expiry_days", "pending_cancel_mode", "pending_cancel_after_days", "wave_max_pct",
    "max_wave_age_hours", "risk_usd", "pp_risk_usd", "contract_size", "magic", "spread",
    "slippage", "adx14_change_enabled", "adx14_equity_gate_enabled", "pnl_base_tracker_enabled",
    "wave_min_sl", "wave_position_enabled", "wave_positions_only", "wave_isolation_study",
    "wave_counter_two_sided_enabled", "two_sided_entry_min_wave_pct",
    "skip_primary_entry_on_parent_wave_enable", "wf_enabled", "pp_enabled", "pp_sl_pct",
    "pp_disabled_in_ext_context", "trend_filter_enabled", "trend_hh_hl_filter_enabled",
    "bos_entry_enable", "wave_size_sl_ladder_base_pct", "wave_size_sl_ladder_step_pct",
    "wave_size_sl_ladder_band_size_pct", "ext_enabled", "ext_wave_min_pct", "ext_secondary_enabled",
    "ext_weekend_gap_relax_factor", "ext_counter_enabled", "ext_counter_time",
    "ext_counter_min_sl_enabled", "ext_counter_min_sl_pct", "ext_trade_both_sides_in_range",
    "wave_min_pct_enable", "ext_post_both_sides_wave_min_pct", "ext_post_both_sides_default_sl_pct",
    "ext_close_trend_positions_on_bos", "wave_allowed_sessions", "wave_custom_window",
    "track_concurrent_positions", "backtest_position_cap_mode", "backtest_max_open_positions",
    "prop_firms_enabled", "prop_firms_presets", "prop_firms_account_size_usd",
    "prop_firms_generate_html",
]

DEFAULTS = {
    "symbol": "EURUSD",
    "sl_fib_level": 0.8,
    "abort_fib_level": "shift_sl",
    "wave_plus": True,
    "max_wave_age_hours": 20,
    "risk_usd": 500.0,
    "contract_size": 100_000.0,
    "magic": 100_001,
    "spread": 0.0001,
    "slippage": 0.0,
    "adx14_change_enabled": False,
    "adx14_equity_gate_enabled": False,
    "wave_2_no_tp_enable": True,
    "wave_2_no_tp_max_index": 2,
    "pnl_base_tracker_enabled": False,
    "prop_firms_enabled": True,
    "prop_firms_presets": "FTMO",
    "prop_firms_account_size_usd": 100_000,
    "prop_firms_generate_html": False,
    "backtest_position_cap_mode": "off",
    "backtest_max_open_positions": None,
    "wave_allowed_sessions": None,
    "wave_custom_window": None,
    "track_concurrent_positions": True,
    "skip_primary_entry_on_parent_wave_enable": True,
    "wf_enabled": True,
    "ext_secondary_enabled": False,
    "ext_trade_both_sides_in_range": True,
    "causal_mode": False,
    "run_e2e_parity": False,
}

INT_KEYS = {
    "magic", "pp_risk_usd", "contract_size", "prop_firms_account_size_usd",
    "pending_cancel_after_days", "tp_target_wave_index", "wave_2_no_tp_max_index",
    "min_opp_bars", "order_expiry_days", "ext_order_expiry_days", "wave_max_pct",
}


def norm(v, k):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, (bool, np.bool_)):
        return bool(v)
    if isinstance(v, pd.Timestamp):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, (np.floating, float)):
        if k in INT_KEYS and float(v) == int(v):
            return int(v)
        return float(v)
    return v


def parse_bot_name(bot_name: str) -> dict:
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
        out["wave_2_no_tp_max_index"] = 2
    return out


def fmt(k, v):
    if isinstance(v, str):
        return f'            "{k}": ["{v}"],'
    if isinstance(v, bool):
        return f'            "{k}": [{str(v)}],'
    if v is None:
        return f'            "{k}": [None],'
    if isinstance(v, int) and k == "prop_firms_account_size_usd":
        return f'            "{k}": [{v:_}],'
    if isinstance(v, int):
        return f'            "{k}": [{v}],'
    return f'            "{k}": [{v}],'


def main() -> None:
    import sys

    xlsx = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
        "results/EURUSD/grid_report 2025-06-10 2026-06-10.xlsx"
    )
    targets = [int(x) for x in (sys.argv[2].split(",") if len(sys.argv) > 2 else "50,53,280,207").split(",")]
    out_path = Path(sys.argv[3]) if len(sys.argv) > 3 else Path("scripts/_generated_example_variation.txt")

    df = pd.read_excel(xlsx, sheet_name="vysledky")
    lines: list[str] = []
    for cn in targets:
        sub = df[df["combo_no"] == cn]
        if sub.empty:
            raise SystemExit(f"combo_no {cn} not found in {xlsx}")
        r = sub.iloc[0]
        parsed = parse_bot_name(str(r["bot_name"]))
        lines.append(f"        {{  # combo_no {cn}")
        for k in CONFIG_KEYS:
            if k in df.columns and pd.notna(r.get(k)):
                v = norm(r[k], k)
            elif k in parsed:
                v = parsed[k]
            else:
                v = DEFAULTS.get(k)
            if k == "wave_min_pct_enable" and v is None:
                v = False
            lines.append(fmt(k, v))
        lines.append("        },")
        lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {len(targets)} blocks -> {out_path}")


if __name__ == "__main__":
    main()
