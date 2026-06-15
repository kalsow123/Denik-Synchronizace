"""Trace EXT3 post-waves: seed, suppression, climax reversal."""
from __future__ import annotations

import pandas as pd

from backtest.grid.backtest_conf import generate_combinations, get_profile
from backtest.grid.translator import grid_dict_to_bot_config
from strategy.ext_range import reapply_ext_range_tags
from strategy.wave_detection_pine import run_pine_wave_simulation
from strategy.wave_sequence import compute_wave_sequence_info_per_wave


def dump_period(label: str, df, waves, birth, seq, t0: str, t1: str) -> None:
    print(f"\n{'='*60}\n{label}\n{'='*60}")
    for w in sorted(waves, key=lambda x: birth.get(str(x["wave_time"]), 0)):
        wt = str(w["wave_time"])
        b = birth.get(wt)
        if b is None:
            continue
        bt = df.iloc[b]["time"]
        if not (pd.Timestamp(t0) <= bt <= pd.Timestamp(t1)):
            continue
        info = seq.get(wt)
        idx = getattr(info, "index_in_trend", None) if info else None
        d = "UP" if int(w.get("dir", 0)) == 1 else "DN"
        parts = [
            str(bt)[5:16],
            d,
            f"idx={idx}",
            f"wt={wt}",
            f"ext={bool(w.get('is_ext'))}",
            f"in_ext={bool(w.get('in_ext_range'))}",
            f"SUPP={bool(w.get('post_ext_trend_suppressed'))}",
            f"seed={w.get('ext_post_trend_seed_dir')}",
            f"seq_bos={getattr(info,'is_bos_wave',False) if info else False}",
        ]
        print("  ".join(parts))


def main() -> None:
    combo = next(
        c
        for c in generate_combinations(get_profile("testing"))
        if c.get("bos_entry_enable")
        and not c.get("pp_enabled")
        and c.get("wave_counter_two_sided_enabled")
    )
    cfg = grid_dict_to_bot_config(combo)
    df = pd.read_csv("data/EURUSD.x_M30.csv", parse_dates=["datetime"]).rename(
        columns={"datetime": "time"}
    )
    df = df[(df["time"] >= "2025-05-10") & (df["time"] <= "2025-06-05")].reset_index(
        drop=True
    )
    waves, birth, _, _ = run_pine_wave_simulation(df, cfg)
    reapply_ext_range_tags(waves, cfg, df=df, wave_birth=birth)
    seq = compute_wave_sequence_info_per_wave(df, waves, cfg)

    dump_period("MAY 21 — EXT3 okno", df, waves, birth, seq, "2025-05-21", "2025-05-22 12:00")
    dump_period("MAY 30 — EXT3 okno (reference)", df, waves, birth, seq, "2025-05-29 20:00", "2025-05-30 12:00")


if __name__ == "__main__":
    main()
