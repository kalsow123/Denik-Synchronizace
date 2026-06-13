#!/usr/bin/env python3
"""Porovna WAVE slice plneho behu vs wave_isolation_study pro jednu kombinaci."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

from backtest.engine import BacktestEngine
from backtest.grid.backtest_conf import generate_combinations, get_profile
from backtest.grid.data_cache import load_data
from backtest.grid.translator import grid_dict_to_bot_config
from backtest.stats import compute_stats, trades_to_df

ROOT = Path(__file__).resolve().parents[1]


def _find_combo(*, combo_no: int | None, bot_name: str | None, profile: str) -> dict:
    combos = generate_combinations(get_profile(profile))
    if combo_no is not None:
        for c in combos:
            if c.get("_grid_test_pozice") == combo_no or c.get("combo_no") == combo_no:
                return copy.deepcopy(c)
    if bot_name:
        for c in combos:
            if c.get("bot_name") == bot_name:
                return copy.deepcopy(c)
    raise SystemExit(f"Kombinace nenalezena (combo_no={combo_no}, bot_name={bot_name})")


def _run(combo: dict) -> dict:
    cfg = grid_dict_to_bot_config(combo)
    df = load_data(
        combo["symbol"],
        combo["timeframe"],
        combo.get("date_from"),
        combo.get("date_to"),
    )
    trades = BacktestEngine(cfg).run(df)
    return compute_stats(trades_to_df(trades))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--profile", default="bot_finish")
    p.add_argument("--combo-no", type=int)
    p.add_argument("--bot-name")
    args = p.parse_args()

    full = _find_combo(combo_no=args.combo_no, bot_name=args.bot_name, profile=args.profile)
    iso = copy.deepcopy(full)
    iso["wave_counter_two_sided_enabled"] = False
    iso["wave_positions_only"] = True
    iso["wave_isolation_study"] = True

    full_s = _run(full)
    iso_s = _run(iso)

    out = {
        "bot_name": full.get("bot_name"),
        "full": {
            "trades_wave": full_s.get("trades_wave"),
            "net_pnl_wave_usd": full_s.get("net_pnl_wave_usd"),
            "trades_wave_counter": full_s.get("trades_wave_counter"),
            "trades_wave_two_sided": full_s.get("trades_wave_two_sided"),
        },
        "isolation": {
            "trades_wave": iso_s.get("trades_wave"),
            "net_pnl_wave_usd": iso_s.get("net_pnl_wave_usd"),
            "trades_wave_counter": iso_s.get("trades_wave_counter"),
            "trades_wave_two_sided": iso_s.get("trades_wave_two_sided"),
        },
        "pass": (
            full_s.get("trades_wave") == iso_s.get("trades_wave")
            and full_s.get("net_pnl_wave_usd") == iso_s.get("net_pnl_wave_usd")
        ),
    }
    print(json.dumps(out, indent=2, ensure_ascii=False))
    raise SystemExit(0 if out["pass"] else 1)


if __name__ == "__main__":
    main()
