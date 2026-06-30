"""Fast May 21 EXT3 diagnostic — waves only, no trade loop."""
from __future__ import annotations

import pandas as pd

from backtest.grid.backtest_conf import generate_combinations, get_profile
from backtest.grid.translator import grid_dict_to_bot_config
from strategy.ext_range import reapply_ext_range_tags
from strategy.trend_bos import (
    _detect_close_bos_timeline_flips,
    collect_bos_flip_events,
    compute_wave_birth_bars_pine,
    reconcile_bos_flip_map_with_wave_sequence,
    compute_bos_wave_flip_map,
)
from strategy.wave_detection_pine import run_pine_wave_simulation
from strategy.wave_sequence import compute_wave_sequence_info_per_wave


def main() -> None:
    combo = next(
        c
        for c in generate_combinations(get_profile("testing"))
        if c.get("bos_entry_enable")
        and not c.get("pp_enabled")
        and c.get("wave_counter_two_sided_enabled")
    )
    cfg = grid_dict_to_bot_config(combo)
    df = pd.read_csv("data/EURUSD_M30.csv", parse_dates=["datetime"]).rename(
        columns={"datetime": "time"}
    )
    df = df[(df["time"] >= "2025-05-10") & (df["time"] <= "2025-06-05")].reset_index(
        drop=True
    )

    waves, birth, _, _ = run_pine_wave_simulation(df, cfg)
    reapply_ext_range_tags(waves, cfg, df=df, wave_birth=birth)
    seq = compute_wave_sequence_info_per_wave(df, waves, cfg)

    flips = _detect_close_bos_timeline_flips(df, waves, cfg, wave_birth_bars=birth)
    flip_map = compute_bos_wave_flip_map(df, waves, cfg, wave_birth_bars=birth)
    flip_map = reconcile_bos_flip_map_with_wave_sequence(
        flip_map, flips, waves, seq, birth
    )
    bos_wt = set(flip_map.values())

    t0, t1 = pd.Timestamp("2025-05-21"), pd.Timestamp("2025-05-23 12:00")
    print("=== WAVES May21-May23 ===")
    rows = []
    for w in waves:
        wt = str(w["wave_time"])
        b = birth.get(wt)
        if b is None:
            continue
        bt = df.iloc[b]["time"]
        if not (t0 <= bt <= t1):
            continue
        info = seq.get(wt)
        idx = getattr(info, "index_in_trend", None) if info else None
        flags = []
        if w.get("is_ext"):
            flags.append("EXT")
        if w.get("in_ext_range"):
            flags.append("in_ext")
        if info and info.is_bos_wave:
            flags.append("seq_BOS")
        if wt in bos_wt:
            flags.append("flip_BOS")
        if w.get("post_ext_trend_suppressed"):
            flags.append("SUPP")
        if w.get("ext_post_trend_seed_dir"):
            flags.append(f"seed={w.get('ext_post_trend_seed_dir')}")
        d = "UP" if int(w.get("dir", 0)) == 1 else "DN"
        rows.append((bt, d, idx, wt, w.get("draw_right"), w.get("box_bottom"), flags))
    for r in sorted(rows):
        bt, d, idx, wt, dr, low, flags = r
        print(f"{bt} {d:2} idx={idx} wt={wt} dr={dr} low={low} {' '.join(flags)}")

    print("\n=== Timeline flips May21-May23 ===")
    t0, t1 = pd.Timestamp("2025-05-21"), pd.Timestamp("2025-05-23 12:00")
    for i, ft in flips:
        t = df.iloc[i]["time"]
        if t0 <= t <= t1:
            print(f"  bar {i} {t} dir={ft} wave={flip_map.get(i)}")

    print("\n=== EXT in period ===")
    for w in waves:
        if not w.get("is_ext"):
            continue
        wt = str(w["wave_time"])
        b = birth.get(wt)
        if b is None:
            continue
        bt = df.iloc[b]["time"]
        if not (t0 <= bt <= t1):
            continue
        info = seq.get(wt)
        print(
            wt,
            bt,
            "dir",
            w.get("dir"),
            "idx",
            getattr(info, "index_in_trend", None),
            "in_ext",
            w.get("in_ext_range"),
        )


if __name__ == "__main__":
    main()
