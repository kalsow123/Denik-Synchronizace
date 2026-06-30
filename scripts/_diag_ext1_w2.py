"""Diag: EXT1 + WAVE2 same dir -> EXT should end, no opposite idx until BOS."""
from __future__ import annotations

import pandas as pd

from backtest.engine import BacktestEngine
from backtest.grid.backtest_conf import generate_combinations, get_profile
from backtest.grid.translator import grid_dict_to_bot_config


def dump(start: str, end: str) -> None:
    cfg = grid_dict_to_bot_config(generate_combinations(get_profile("testing"))[0])
    df = pd.read_csv("data/EURUSD_M30.csv", parse_dates=["datetime"]).rename(
        columns={"datetime": "time"}
    )
    df = df[(df["time"] >= start) & (df["time"] <= end)].reset_index(drop=True)
    eng = BacktestEngine(cfg)
    eng.run(df.copy(), retain_wave_snapshot=True)
    print(f"=== {start} .. {end} ===")
    for w in sorted(
        eng.last_waves,
        key=lambda x: (int(x.get("draw_right", 0)), str(x.get("wave_time", ""))),
    ):
        wt = str(w["wave_time"])
        dr = int(w.get("draw_right", 0))
        if dr >= len(df):
            continue
        bt = df.iloc[dr]["time"]
        info = eng.wave_sequence_info.get(wt)
        idx = info.index_in_trend if info else None
        bos = info.is_bos_wave if info else False
        d = "UP" if int(w.get("dir", 0)) == 1 else "DN"
        flags = []
        if w.get("is_ext"):
            flags.append("EXT")
        if w.get("in_ext_range"):
            flags.append("in_ext")
        if w.get("ext_post_range_terminator"):
            flags.append("TERM")
        if wt in (eng._bos_wave_times or set()):
            flags.append("bos_wt")
        print(
            f"{bt} {d:2} idx={idx} bos={bos} "
            f"top={w.get('box_top')} bot={w.get('box_bottom')} "
            f"{' '.join(flags)} wt={wt}"
        )


if __name__ == "__main__":
    dump("2025-04-21", "2025-04-24")
