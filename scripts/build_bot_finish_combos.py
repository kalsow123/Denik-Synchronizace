#!/usr/bin/env python3
"""Obnovi bot_finish_combos.json z grid_report.xlsx (Ranking_FTMO, top N)."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from backtest.grid.backtest_conf import generate_combinations, get_profile

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    p = argparse.ArgumentParser(description="Export top N kombinaci do bot_finish_combos.json")
    p.add_argument(
        "xlsx",
        type=Path,
        help="Cesta k grid_report.xlsx (napr. results/.../grid_report.xlsx)",
    )
    p.add_argument("-n", "--top", type=int, default=300, help="Pocet kombinaci (default 300)")
    p.add_argument(
        "--metric",
        default="projected_net_pnl_at_max_risk_usd",
        help="Sloupec v listu Ranking_FTMO",
    )
    p.add_argument(
        "--sheet",
        default="Ranking_FTMO",
        help="Excel list s rankingem",
    )
    p.add_argument(
        "--source-profile",
        default="bot_optimalisation",
        help="Profil pro doplneni plnych combo dict podle bot_name",
    )
    args = p.parse_args()

    xlsx = args.xlsx if args.xlsx.is_absolute() else ROOT / args.xlsx
    rank = pd.read_excel(xlsx, sheet_name=args.sheet)
    rank = rank.sort_values(args.metric, ascending=False).head(args.top)
    by_name = {
        c["bot_name"]: c
        for c in generate_combinations(get_profile(args.source_profile))
    }
    found = [by_name[n] for n in rank["bot_name"].astype(str)]

    finish_base = get_profile("bot_finish").get("base", {})
    skip = set(finish_base.keys())

    def export_combo(c: dict) -> dict:
        out = {k: v for k, v in c.items() if k not in skip and not str(k).startswith("__")}
        if "__grid_name_keys" in c:
            out["__grid_name_keys"] = [
                k for k in c["__grid_name_keys"] if k not in skip
            ]
        return out

    out_json = ROOT / "backtest" / "grid" / "bot_finish_combos.json"
    out_csv = ROOT / "backtest" / "grid" / "bot_finish_top300_index.csv"
    payload = {
        "source": str(xlsx.relative_to(ROOT)).replace("\\", "/")
        if xlsx.is_relative_to(ROOT)
        else str(xlsx),
        "ranking_metric": args.metric,
        "sheet": args.sheet,
        "count": len(found),
        "profile_base_keys": sorted(finish_base.keys()),
        "combos": [export_combo(c) for c in found],
    }
    out_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    rank[
        [
            "combo_no",
            "bot_name",
            "projected_net_pnl_at_max_risk_usd",
            "original_net_pnl_usd",
            "profit_factor",
        ]
    ].to_csv(out_csv, index=False, sep=";")
    print(f"Zapsano {len(found)} kombinaci -> {out_json}")
    print(f"Index -> {out_csv}")


if __name__ == "__main__":
    main()
