#!/usr/bin/env python3
"""Prida wave study kombinace do bot_finish_combos.json (wave_only / wave_pp, N=4,6,8,10)."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

import pandas as pd

from backtest.grid.backtest_conf import generate_combinations, get_profile

ROOT = Path(__file__).resolve().parents[1]

DEFAULT_SOURCE_NOS = [
    5148, 25500, 4826, 5145, 25564, 5147, 15570, 15113, 25561, 25994,
    15553, 15708, 15306, 5265, 5339, 4883, 25691,
]
TP_INDICES = [4, 6, 8, 10]


def _apply_wave_isolation(c: dict, *, pp_enabled: bool) -> None:
    """Zachova PP/BOS/EXT/tp_mode ze zdroje; report jen WAVE, engine plna simulace."""
    c["wave_positions_only"] = True
    c["wave_isolation_study"] = True
    c["wave_counter_two_sided_enabled"] = False
    c["pp_enabled"] = pp_enabled


def _is_wave_study_combo(c: dict) -> bool:
    if c.get("finish_variant") in ("wave_only", "wave_pp"):
        return True
    return bool(c.get("wave_isolation_study")) and c.get("source_combo_no") is not None


def export_combo(c: dict, *, skip: set[str]) -> dict:
    out = {k: v for k, v in c.items() if k not in skip and not str(k).startswith("__")}
    if "__grid_name_keys" in c:
        out["__grid_name_keys"] = [k for k in c["__grid_name_keys"] if k not in skip]
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument(
        "xlsx",
        type=Path,
        nargs="?",
        default=ROOT
        / "results/EURUSD/grid_bot_optimalisation_M30_2026-01-01_2026-05-10_006/grid_report.xlsx",
    )
    p.add_argument(
        "--replace-top300",
        action="store_true",
        help="Nahradit existujici JSON jen wave study (default: append k top 300)",
    )
    p.add_argument(
        "--refresh-wave-study",
        action="store_true",
        help="Odstranit stare wave study kombinace a nahradit novymi",
    )
    args = p.parse_args()

    xlsx = args.xlsx if args.xlsx.is_absolute() else ROOT / args.xlsx
    res = pd.read_excel(xlsx, sheet_name="vysledky")
    sub = res[res["combo_no"].isin(DEFAULT_SOURCE_NOS)].sort_values("combo_no")
    if len(sub) != len(DEFAULT_SOURCE_NOS):
        missing = set(DEFAULT_SOURCE_NOS) - set(sub["combo_no"].tolist())
        raise SystemExit(f"Chybi combo_no v reportu: {sorted(missing)}")

    by_name = {
        c["bot_name"]: copy.deepcopy(c)
        for c in generate_combinations(get_profile("bot_optimalisation"))
    }

    base_finish = get_profile("bot_finish").get("base", {})
    skip = set(base_finish.keys())

    new_rows: list[dict] = []
    meta: list[dict] = []
    for _, row in sub.iterrows():
        src_no = int(row["combo_no"])
        src = copy.deepcopy(by_name[str(row["bot_name"])])
        for variant, pp_on in (("wave_only", False), ("wave_pp", True)):
            for n in TP_INDICES:
                c = copy.deepcopy(src)
                _apply_wave_isolation(c, pp_enabled=pp_on)
                c["tp_mode"] = "wave_target_n"
                c["tp_target_wave_index"] = int(n)
                c["finish_variant"] = variant
                c["source_combo_no"] = src_no
                new_rows.append(export_combo(c, skip=skip))
                meta.append(
                    {
                        "source_combo_no": src_no,
                        "finish_variant": variant,
                        "tp_target_wave_index": n,
                        "wave_isolation_study": True,
                    }
                )

    out_json = ROOT / "backtest" / "grid" / "bot_finish_combos.json"
    if args.replace_top300:
        merged = new_rows
    elif args.refresh_wave_study and out_json.is_file():
        existing = json.loads(out_json.read_text(encoding="utf-8"))
        kept = [c for c in existing.get("combos", []) if not _is_wave_study_combo(c)]
        merged = kept + new_rows
    elif out_json.is_file() and not args.refresh_wave_study:
        existing = json.loads(out_json.read_text(encoding="utf-8"))
        merged = existing.get("combos", []) + new_rows
    else:
        merged = new_rows

    payload = {
        "source_xlsx": str(xlsx.relative_to(ROOT)).replace("\\", "/")
        if xlsx.is_relative_to(ROOT)
        else str(xlsx),
        "wave_study_source_combo_nos": DEFAULT_SOURCE_NOS,
        "wave_study_tp_target_wave_index": TP_INDICES,
        "wave_study_added": len(new_rows),
        "count": len(merged),
        "profile_base_keys": sorted(base_finish.keys()),
        "combos": merged,
    }
    out_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    pd.DataFrame(meta).to_csv(
        ROOT / "backtest" / "grid" / "bot_finish_wave_study_index.csv",
        index=False,
        sep=";",
    )
    print(f"Wave study: +{len(new_rows)} -> celkem {len(merged)} v {out_json}")


if __name__ == "__main__":
    main()
