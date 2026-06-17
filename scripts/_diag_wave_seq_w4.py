"""Trace wave_sequence state around WAVE4."""
from __future__ import annotations

import pandas as pd

from backtest.grid.backtest_conf import generate_combinations, get_profile
from backtest.grid.translator import grid_dict_to_bot_config
from strategy.ext_range import reapply_ext_range_tags
from strategy.wave_detection_pine import run_pine_wave_simulation
from strategy.wave_sequence import compute_wave_sequence_info_per_wave


def main() -> None:
    cfg = grid_dict_to_bot_config(generate_combinations(get_profile("testing"))[0])
    df = pd.read_csv("data/EURUSD.x_M30.csv", parse_dates=["datetime"]).rename(
        columns={"datetime": "time"}
    )
    df = df[(df["time"] >= "2025-05-10") & (df["time"] <= "2025-05-28")].reset_index(
        drop=True
    )
    waves, birth, _, _ = run_pine_wave_simulation(df, cfg)
    reapply_ext_range_tags(waves, cfg, df=df, wave_birth=birth)
    seq = compute_wave_sequence_info_per_wave(df, waves, cfg)

    t0, t1 = pd.Timestamp("2025-05-21"), pd.Timestamp("2025-05-26")
    for w in waves:
        wt = str(w["wave_time"])
        dr = int(w.get("draw_right", 0))
        if dr < 0 or dr >= len(df):
            continue
        bt = df.iloc[dr]["time"]
        if not (t0 <= bt <= t1):
            continue
        info = seq.get(wt)
        idx = getattr(info, "index_in_trend", None) if info else None
        bos = getattr(info, "is_bos_wave", False) if info else False
        d = "UP" if int(w.get("dir", 0)) == 1 else "DN"
        flags = []
        if w.get("is_ext"):
            flags.append("EXT")
        if w.get("in_ext_range"):
            flags.append("in_ext")
        if w.get("ext_post_range_terminator"):
            flags.append("TERM")
        if w.get("ext_post_trend_seed_dir"):
            flags.append(f"seed={w.get('ext_post_trend_seed_dir')}")
        if w.get("post_ext_trend_suppressed"):
            flags.append("SUPP")
        print(f"{bt} {d} idx={idx} bos={bos} wt={wt} {' '.join(flags)}")


if __name__ == "__main__":
    main()
