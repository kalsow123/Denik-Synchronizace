#!/usr/bin/env python3
"""Conditional redundancy: which settings are dead when parent flags are off."""
from __future__ import annotations

from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]

OUTCOME_COLS = [
    "trades", "trades_wave", "trades_wave_counter", "trades_wave_two_sided",
    "trades_pp", "trades_ext", "trades_bos", "trades_ext_bos",
    "net_pnl_wave_usd", "net_pnl_wave_counter_usd", "net_pnl_wave_two_sided_usd",
    "net_pnl_pp_usd", "net_pnl_ext_usd", "net_pnl_bos_usd", "net_pnl_ext_bos_usd",
    "net_pnl_usd", "profit_factor", "max_dd_%_vs_initial", "max_dd_%_vs_initial_wave",
    "win_rate_%",
]


def load(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, sep=";", low_memory=False)
    if len(df.columns) <= 3:
        df = pd.read_csv(path, low_memory=False)
    for c in OUTCOME_COLS:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c].astype(str).str.replace(",", "."), errors="coerce")
    return df


def dup_groups(df: pd.DataFrame) -> list[pd.DataFrame]:
    cols = [c for c in OUTCOME_COLS if c in df.columns]
    df = df.copy()
    df["_fp"] = df[cols].astype(str).agg("|".join, axis=1)
    return [g for _, g in df.groupby("_fp", dropna=False) if len(g) > 1]


def varying_in_group(g: pd.DataFrame, col: str) -> bool:
    return col in g.columns and g[col].nunique(dropna=False) > 1


def analyze_profile(name: str, path: Path) -> None:
    print("\n" + "=" * 80)
    print(name)
    print("=" * 80)
    df = load(path)
    groups = dup_groups(df)
    print(f"rows={len(df)}, dup_groups={len(groups)}")

    # 1) rrr redundant when tp_mode not rrr_fixed
    rrr_dead = Counter()
    for g in groups:
        if not varying_in_group(g, "rrr"):
            continue
        modes = set(g["tp_mode"].astype(str).unique()) if "tp_mode" in g.columns else set()
        if modes <= {"bos_exit", "wave_target_n", "wave_target_n_g"}:
            rrr_dead["tp_mode != rrr_fixed"] += 1
        elif "rrr_fixed" in modes:
            rrr_dead["includes rrr_fixed"] += 1

    print("\nRRR redundancy:")
    for k, v in rrr_dead.most_common():
        print(f"  {k}: {v} groups")

    # 2) tp_target_wave_index dead when tp_mode not wave_target_n*
    tpidx_dead = Counter()
    for g in groups:
        if not varying_in_group(g, "tp_target_wave_index"):
            continue
        modes = set(g["tp_mode"].astype(str).unique()) if "tp_mode" in g.columns else set()
        if modes <= {"bos_exit", "rrr_fixed"}:
            tpidx_dead["tp_mode not wave_target_n*"] += 1
        else:
            tpidx_dead["includes wave_target_n*"] += 1
    print("\ntp_target_wave_index redundancy:")
    for k, v in tpidx_dead.most_common():
        print(f"  {k}: {v} groups")

    # 3) bos_entry_enable dead
    bos_dead = Counter()
    for g in groups:
        if not varying_in_group(g, "bos_entry_enable"):
            continue
        modes = set(g["tp_mode"].astype(str).unique()) if "tp_mode" in g.columns else set()
        if "rrr_fixed" not in modes:
            bos_dead["tp_mode != rrr_fixed (bos_entry only for rrr_fixed)"] += 1
        else:
            bos_dead["includes rrr_fixed"] += 1
    print("\nbos_entry_enable redundancy:")
    for k, v in bos_dead.most_common():
        print(f"  {k}: {v} groups")

    # 4) ext_counter_enabled dead when ext_enabled=False
    ext_ctr_dead = Counter()
    for g in groups:
        if not varying_in_group(g, "ext_counter_enabled"):
            continue
        ext = set(g["ext_enabled"].astype(str).unique()) if "ext_enabled" in g.columns else set()
        if ext <= {"False", "false", "0"}:
            ext_ctr_dead["ext_enabled=False"] += 1
        else:
            ext_ctr_dead["ext_enabled=True (still same outcome!)"] += 1
    print("\next_counter_enabled redundancy:")
    for k, v in ext_ctr_dead.most_common():
        print(f"  {k}: {v} groups")

    # 5) pp_enabled dead
    pp_dead = Counter()
    for g in groups:
        if not varying_in_group(g, "pp_enabled"):
            continue
        pp_trades = g["trades_pp"].max() if "trades_pp" in g.columns else 0
        if pp_trades == 0:
            pp_dead["trades_pp=0 (PP never opened)"] += 1
        else:
            pp_dead["trades_pp>0 but same total outcome"] += 1
    print("\npp_enabled redundancy:")
    for k, v in pp_dead.most_common():
        print(f"  {k}: {v} groups")

    # 6) ext_close_trend_positions_on_bos
    if "ext_close_trend_positions_on_bos" in df.columns:
        ext_close_dead = 0
        for g in groups:
            if varying_in_group(g, "ext_close_trend_positions_on_bos"):
                ext_close_dead += 1
        print(f"\next_close_trend_positions_on_bos varies in dup groups: {ext_close_dead}")

    # 7) wave_isolation vs full pairs
    if "study_mode" in df.columns:
        iso_full = 0
        for g in groups:
            modes = set(g["study_mode"].astype(str).unique())
            if modes == {"full", "wave_isolation"}:
                iso_full += 1
        print(f"\nfull + wave_isolation same outcome groups: {iso_full}")

    # 8) theoretical combo count reduction for bot_optimalisation
    if "tp_mode" in df.columns:
        total = len(df)
        by_mode = df.groupby("tp_mode").size()
        print("\nRows by tp_mode:")
        for m, n in by_mode.items():
            print(f"  {m}: {n}")

        # estimate if we fix grid
        # rrr only for rrr_fixed: currently 3 values for all modes
        # tp_target only for wave_target_n*: 3 values for all modes
        print("\nGrid dimension estimate (bot_optimalisation profile):")
        dims = {
            "rrr": 3,
            "tp_mode": 4,
            "tp_target_wave_index": 3,
            "fib_level": 3,
            "sl_fib_level": 3,
            "wave_counter_two_sided_enabled": 2,
            "pp_enabled": 2,
            "bos_entry_enable": 2,
            "ext_enabled": 2,
            "ext_counter_enabled": 2,
            "ext_close_trend_positions_on_bos": 2,
        }
        full_product = 1
        for v in dims.values():
            full_product *= v
        print(f"  theoretical product (main toggles): {full_product}")
        print(f"  actual rows: {total}")
        print(f"  unique outcomes: {len(df.groupby(df[[c for c in OUTCOME_COLS if c in df.columns]].astype(str).agg('|'.join, axis=1)))}")


def main() -> None:
    analyze_profile(
        "bot_finish _007",
        ROOT / "results/EURUSD/grid_bot_finish_M30_2026-01-01_2026-05-10_007/grid_report.csv",
    )
    analyze_profile(
        "bot_optimalisation _006",
        ROOT / "results/EURUSD/grid_bot_optimalisation_M30_2026-01-01_2026-05-10_006/grid_report.csv",
    )


if __name__ == "__main__":
    main()
