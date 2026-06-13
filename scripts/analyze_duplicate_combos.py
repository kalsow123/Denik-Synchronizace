#!/usr/bin/env python3
"""Find combos with identical backtest outcomes and which settings are redundant."""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]

OUTCOME_COLS = [
    "trades",
    "trades_wave",
    "trades_wave_counter",
    "trades_wave_two_sided",
    "trades_pp",
    "trades_ext",
    "trades_bos",
    "trades_ext_bos",
    "net_pnl_wave_usd",
    "net_pnl_wave_counter_usd",
    "net_pnl_wave_two_sided_usd",
    "net_pnl_pp_usd",
    "net_pnl_ext_usd",
    "net_pnl_bos_usd",
    "net_pnl_ext_bos_usd",
    "net_pnl_usd",
    "profit_factor",
    "max_dd_%_vs_initial",
    "max_dd_%_vs_initial_wave",
    "win_rate_%",
]

META_SKIP = {
    "combo_no",
    "bot_name",
    "paired_full_combo_no",
    "error",
    "traceback",
    "prop_firm_pass_count",
    "prop_firm_best_match",
}


def _to_num(s: pd.Series) -> pd.Series:
    if s.dtype == object:
        s = s.astype(str).str.replace(",", ".", regex=False)
    return pd.to_numeric(s, errors="coerce")


def load_report(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, sep=";", low_memory=False)
    if len(df.columns) <= 3:
        df = pd.read_csv(path, low_memory=False)
    for c in OUTCOME_COLS:
        if c in df.columns:
            df[c] = _to_num(df[c])
    return df


def analyze(name: str, path: Path) -> dict:
    print("\n" + "=" * 80)
    print(name)
    print("=" * 80)
    df = load_report(path)
    print(f"rows: {len(df)}")

    outcome_cols = [c for c in OUTCOME_COLS if c in df.columns]
    outcome_set = set(outcome_cols)
    config_cols = [
        c
        for c in df.columns
        if c not in outcome_set
        and c not in META_SKIP
        and not c.startswith("FTMO__")
        and not c.startswith("max_dd_date")
        and not c.startswith("max_daily_dd")
        and c not in {"max_dd_usd", "max_dd_%", "sharpe", "cagr_pct", "calmar", "sortino",
                      "profitable_months_pct", "longest_loss_streak_trades",
                      "longest_loss_streak_days", "max_pos_open", "max_pos_open_count",
                      "second_max_pos_open", "second_max_pos_open_count", "net_pnl_non_pp_usd"}
    ]

    df["_fp"] = df[outcome_cols].astype(str).agg("|".join, axis=1)
    sizes = df.groupby("_fp", dropna=False).size()
    dup_sizes = sizes[sizes > 1]

    print(f"unique outcome fingerprints: {len(sizes)}")
    print(f"duplicate outcome groups: {len(dup_sizes)}")
    print(f"rows in duplicate groups: {int(dup_sizes.sum())}")
    print(f"potential savings (skip redundant runs): {int(dup_sizes.sum() - len(dup_sizes))}")

    if "study_mode" in df.columns:
        print("study_mode:", df["study_mode"].value_counts().to_dict())

    pattern_counter: Counter[tuple] = Counter()
    examples: list[dict] = []

    for fp_val, g in df.groupby("_fp", dropna=False):
        if len(g) < 2:
            continue
        varying_cols = []
        for c in config_cols:
            if c not in g.columns:
                continue
            vals = g[c].dropna().unique()
            if len(vals) > 1:
                varying_cols.append((c, tuple(sorted(str(v) for v in vals))))
        key = tuple(sorted(varying_cols, key=lambda x: x[0]))
        pattern_counter[key] += 1
        if len(examples) < 8:
            examples.append(
                {
                    "n": len(g),
                    "combo_nos": sorted(g["combo_no"].tolist())[:10],
                    "varying": varying_cols,
                    "net_pnl_usd": float(g["net_pnl_usd"].iloc[0]),
                    "trades": int(g["trades"].iloc[0]),
                    "study_modes": sorted(g["study_mode"].unique().tolist())
                    if "study_mode" in g.columns
                    else [],
                }
            )

    print("\nTop redundant setting patterns:")
    for i, (pattern, cnt) in enumerate(pattern_counter.most_common(20), 1):
        cols = ", ".join(f"{c}={list(vals)}" for c, vals in pattern[:8])
        more = "" if len(pattern) <= 8 else f" (+{len(pattern) - 8} more)"
        print(f"  {i}. {cnt}x groups | {cols}{more}")

    print("\nConcrete examples:")
    for i, ex in enumerate(examples, 1):
        print(
            f"  {i}. {ex['n']} combos | net_pnl={ex['net_pnl_usd']} trades={ex['trades']} "
            f"| study_modes={ex['study_modes']} | combo_nos={ex['combo_nos']}"
        )
        for c, vals in ex["varying"][:10]:
            print(f"      {c}: {list(vals)}")

    # Per-column redundancy: how often does changing this col alone (within dup group) not change outcome
    col_freq: Counter[str] = Counter()
    for fp_val, g in df.groupby("_fp", dropna=False):
        if len(g) < 2:
            continue
        for c in config_cols:
            if c in g.columns and g[c].nunique(dropna=False) > 1:
                col_freq[c] += 1

    print("\nSettings most often irrelevant (vary within duplicate groups):")
    for c, n in col_freq.most_common(25):
        print(f"  {c}: {n} duplicate groups")

    return {
        "rows": len(df),
        "unique": len(sizes),
        "dup_groups": len(dup_sizes),
        "savings": int(dup_sizes.sum() - len(dup_sizes)),
        "top_cols": col_freq.most_common(15),
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--finish", type=Path, default=ROOT / "results/EURUSD/grid_bot_finish_M30_2026-01-01_2026-05-10_007/grid_report.csv")
    p.add_argument("--opt", type=Path, default=ROOT / "results/EURUSD/grid_bot_optimalisation_M30_2026-01-01_2026-05-10_006/grid_report.csv")
    args = p.parse_args()

    r1 = analyze("bot_finish _007", args.finish)
    r2 = analyze("bot_optimalisation _006", args.opt)

    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"bot_finish: {r1['rows']} runs -> {r1['unique']} unique outcomes ({r1['savings']} redundant)")
    print(f"bot_optimalisation: {r2['rows']} runs -> {r2['unique']} unique outcomes ({r2['savings']} redundant)")


if __name__ == "__main__":
    main()
