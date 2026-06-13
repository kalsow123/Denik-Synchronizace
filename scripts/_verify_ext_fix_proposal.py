"""Simulace navržených EXT oprav na May 19 / May 29."""
from __future__ import annotations

import pandas as pd

from backtest.grid.backtest_conf import generate_combinations, get_profile
from backtest.grid.translator import grid_dict_to_bot_config
from strategy.wave_sequence import compute_wave_sequence_info_per_wave


def _cfg():
    return grid_dict_to_bot_config(
        next(
            c
            for c in generate_combinations(get_profile("testing"))
            if c.get("trend_hh_hl_filter_enabled")
            and not c.get("wave_counter_two_sided_enabled")
        )
    )


def _run(label, t0, t1, watch):
    cfg = _cfg()
    df = pd.read_csv("data/EURUSD.x_M30.csv", parse_dates=["datetime"]).rename(
        columns={"datetime": "time"}
    )
    df = df[(df["time"] >= t0) & (df["time"] <= t1)].reset_index(drop=True)
    from backtest.engine import BacktestEngine

    eng = BacktestEngine(cfg)
    eng.run(df, retain_wave_snapshot=True)
    print(f"\n=== {label} (aktuální stav) ===")
    for wt in watch:
        info = eng.wave_sequence_info.get(wt)
        print(f"  {wt}: idx={info.index_in_trend if info else None} bos={info.is_bos_wave if info else None}")


if __name__ == "__main__":
    _run(
        "May 19",
        "2025-05-14",
        "2025-05-20",
        ["202505190400"],
    )
    _run(
        "May 29",
        "2025-05-28",
        "2025-05-31",
        ["202505290230", "202505290530", "202505291300", "202505292100"],
    )
