"""Diagnostika: EXT3 → WAVE4 → WAVE_BOS → bear chain (May 21–26)."""
from __future__ import annotations

import pandas as pd

from backtest.engine import BacktestEngine
from backtest.grid.backtest_conf import generate_combinations, get_profile
from backtest.grid.translator import grid_dict_to_bot_config
from strategy.trend_bos import (
    compute_bos_wave_flip_map,
    iter_close_based_bos_flips,
)


def main() -> None:
    cfg = grid_dict_to_bot_config(generate_combinations(get_profile("testing"))[0])
    df = pd.read_csv("data/EURUSD_M30.csv", parse_dates=["datetime"]).rename(
        columns={"datetime": "time"}
    )
    df = df[(df["time"] >= "2025-05-10") & (df["time"] <= "2025-05-28")].reset_index(
        drop=True
    )
    eng = BacktestEngine(cfg)
    eng.run(df.copy(), retain_wave_snapshot=True)

    t0, t1 = pd.Timestamp("2025-05-21"), pd.Timestamp("2025-05-26 18:00")
    print("=== WAVES May21-26 ===")
    for w in sorted(
        eng.last_waves,
        key=lambda x: (int(x.get("draw_right", 0)), str(x.get("wave_time", ""))),
    ):
        wt = str(w["wave_time"])
        dr = int(w.get("draw_right", 0))
        if dr < 0 or dr >= len(df):
            continue
        bt = df.iloc[dr]["time"]
        if not (t0 <= bt <= t1):
            continue
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
        if w.get("post_ext_trend_suppressed"):
            flags.append("SUPP")
        if wt in (eng._bos_wave_times or set()):
            flags.append("bos_wt")
        vis = eng._visual_bos_wave_times or eng._bos_wave_times or set()
        if wt in vis:
            flags.append("vis_bos")
        print(
            f"{bt} {d:2} idx={idx} bos={bos} wt={wt} dr={dr} "
            f"low={w.get('box_bottom')} flags={' '.join(flags)}"
        )

    print("\n=== close-BOS flips May21-26 ===")
    for item in iter_close_based_bos_flips(df, eng.last_waves, cfg):
        i, t, target, label, *_rest = item
        if t0 <= pd.Timestamp(t) <= t1:
            print(i, t, target, label)

    print("\n=== flip map May21-26 ===")
    birth = getattr(eng, "_wave_birth_bars", {}) or {}
    fmap = compute_bos_wave_flip_map(df, eng.last_waves, cfg, wave_birth_bars=birth)
    for bar, wt in sorted(fmap.items()):
        if bar < 0 or bar >= len(df):
            continue
        t = df.iloc[bar]["time"]
        if t0 <= t <= t1:
            info = eng.wave_sequence_info.get(wt)
            print(
                "bar",
                bar,
                t,
                "bos_wave",
                wt,
                "idx",
                info.index_in_trend if info else None,
            )

    w4 = "202505211700"
    if w4 in eng.waves_by_wave_time:
        w = eng.waves_by_wave_time[w4]
        dr = int(w["draw_right"])
        low = float(w["box_bottom"])
        print(f"\n=== close pod WAVE4 low ({low}) po dr={dr} ===")
        for j in range(dr, min(dr + 80, len(df))):
            c = float(df.iloc[j]["close"])
            if c < low:
                print("  first break:", df.iloc[j]["time"], "bar", j, "close", c)
                print("  in _close_bos_flip_bar_indices:", j in (eng._close_bos_flip_bar_indices or set()))
                break


if __name__ == "__main__":
    main()
