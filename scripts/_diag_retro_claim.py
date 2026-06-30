"""Debug retro claim at bar 407 in full wave_sequence."""
from __future__ import annotations

import pandas as pd

from backtest.grid.backtest_conf import generate_combinations, get_profile
from backtest.grid.translator import grid_dict_to_bot_config
from strategy.ext_range import reapply_ext_range_tags
from strategy.trend_bos import TrendState, _detect_close_bos_timeline_flips
from strategy.wave_detection_pine import run_pine_wave_simulation
from strategy.wave_sequence import _retro_claim_bos_seed_wave, compute_wave_sequence_info_per_wave


def main() -> None:
    cfg = grid_dict_to_bot_config(generate_combinations(get_profile("testing"))[0])
    df = pd.read_csv("data/EURUSD_M30.csv", parse_dates=["datetime"]).rename(
        columns={"datetime": "time"}
    )
    df = df[(df["time"] >= "2025-05-10") & (df["time"] <= "2025-05-28")].reset_index(
        drop=True
    )
    waves, birth, _, _ = run_pine_wave_simulation(df, cfg)
    reapply_ext_range_tags(waves, cfg, df=df, wave_birth=birth)
    seq = compute_wave_sequence_info_per_wave(df, waves, cfg)

    flip_bar = 407
    claimed = _retro_claim_bos_seed_wave(seq, waves, flip_bar, "bear")
    print("retro claim at 407 bear:", claimed)

    flips = _detect_close_bos_timeline_flips(df, waves, cfg, wave_birth_bars=birth)
    may_flips = [(i, ft) for i, ft in flips if 360 <= i <= 450]
    print("timeline flips 360-450:", may_flips)

    for wt in ["202505220730", "202505212300", "202505222000"]:
        info = seq.get(wt)
        print(wt, info)


if __name__ == "__main__":
    main()
