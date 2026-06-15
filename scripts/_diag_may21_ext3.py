"""Diagnose May 21 EXT3 -> WAVE4 -> BOS scenario."""
from __future__ import annotations

import pandas as pd

from backtest.engine import BacktestEngine
from backtest.grid.backtest_conf import generate_combinations, get_profile
from backtest.grid.translator import grid_dict_to_bot_config
from strategy.trend_bos import (
    _detect_close_bos_timeline_flips,
    collect_bos_flip_events,
)


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
    df = df[
        (df["time"] >= combo["date_from"]) & (df["time"] <= combo["date_to"])
    ].reset_index(drop=True)

    t0, t1 = pd.Timestamp("2025-05-20"), pd.Timestamp("2025-05-31")
    eng = BacktestEngine(cfg)
    eng.run(df.copy(), retain_wave_snapshot=True)
    birth = eng.wave_birth_by_time
    seq = eng.wave_sequence_info
    bos = set(eng._bos_wave_times or ())

    print("=== WAVES May20-May31 (by birth) ===")
    rows = []
    for w in eng._all_waves:
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
        if wt in bos:
            flags.append("rt_BOS")
        if w.get("post_ext_trend_suppressed"):
            flags.append("SUPP")
        if w.get("ext_post_trend_seed_dir"):
            flags.append(f"seed={w.get('ext_post_trend_seed_dir')}")
        d = "UP" if int(w.get("dir", 0)) == 1 else "DN"
        rows.append((bt, d, idx, wt, w.get("draw_right"), w.get("box_bottom"), flags))
    for r in sorted(rows):
        bt, d, idx, wt, dr, low, flags = r
        print(f"{bt} {d:2} idx={idx} wt={wt} dr={dr} low={low} {' '.join(flags)}")

    print("\n=== BOS flip events May20-May31 ===")
    for ev in collect_bos_flip_events(df, eng._all_waves, cfg):
        t = pd.Timestamp(ev[0])
        if t0 <= t <= t1:
            print(f"  {t} swing={ev[1]}")

    print("\n=== Timeline flips ===")
    for i, ft in _detect_close_bos_timeline_flips(
        df, eng._all_waves, cfg, wave_birth_bars=birth
    ):
        t = df.iloc[i]["time"]
        if t0 <= t <= t1:
            print(f"  bar {i} {t} dir={ft}")

    # Focus EXT3 on May 21 area
    print("\n=== EXT waves in period ===")
    for w in eng._all_waves:
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
