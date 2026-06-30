"""Diagnostika EXT BOS fib 0.35 u EXT idx=3 kolem May 26."""
from __future__ import annotations

import pandas as pd

from backtest.engine import BacktestEngine
from backtest.grid.backtest_conf import generate_combinations, get_profile
from backtest.grid.translator import grid_dict_to_bot_config


def main() -> None:
    combos = generate_combinations(get_profile("testing"))
    combo = next(
        c
        for c in combos
        if c.get("wave_counter_two_sided_enabled")
        and c.get("trend_hh_hl_filter_enabled")
    )
    cfg = grid_dict_to_bot_config(combo)
    df = pd.read_csv("data/EURUSD_M30.csv", parse_dates=["datetime"]).rename(
        columns={"datetime": "time"}
    )
    df = df[(df["time"] >= "2025-05-10") & (df["time"] <= "2025-05-27")].reset_index(
        drop=True
    )

    eng = BacktestEngine(cfg)
    eng.run(df, retain_wave_snapshot=True)
    fib = float(getattr(cfg, "ext_bos_fib_level", 0.35))

    print("=== EXT waves idx=3 May 24-27 ===")
    for w in eng._all_waves:
        wt = str(w["wave_time"])
        if not w.get("is_ext"):
            continue
        info = eng.wave_sequence_info.get(wt)
        idx = info.index_in_trend if info else w.get("index_in_trend")
        if idx != 3:
            continue
        dl, dr = int(w["draw_left"]), int(w["draw_right"])
        if dl >= len(df) or dr >= len(df):
            continue
        right_t = df.iloc[dr]["time"]
        if right_t < pd.Timestamp("2025-05-24"):
            continue

        bt = float(w["box_top"])
        bb = float(w["box_bottom"])
        rng = bt - bb
        wdir = int(w["dir"])
        d = "UP" if wdir == 1 else "DN"
        bos = w.get("ext_bos_level")
        seg = df.iloc[dl : dr + 1]
        max_h = float(seg["high"].max())
        min_l = float(seg["low"].min())
        max_h_bar = df.iloc[seg["high"].astype(float).idxmax()]["time"]
        min_l_bar = df.iloc[seg["low"].astype(float).idxmin()]["time"]

        print(f"\n--- {wt} {d} idx=3")
        print(f"  span {df.iloc[dl].time} .. {right_t}")
        print(f"  draw_left={dl} draw_right={dr} birth={eng.wave_birth_by_time.get(wt)}")
        print(f"  box_top={bt:.5f} box_bottom={bb:.5f} range={rng:.5f}")
        print(f"  ext_bos_level={float(bos):.5f}" if bos is not None else "  ext_bos_level=None")
        print(f"  seg max_high={max_h:.5f} at {max_h_bar}")
        print(f"  seg min_low={min_l:.5f} at {min_l_bar}")
        print(f"  box_top vs max_high diff={max_h - bt:.5f}")
        print(f"  box_bottom vs min_low diff={bb - min_l:.5f}")

        if wdir == 1:
            from_box = bt - rng * fib
            from_seg = max_h - (max_h - bb) * fib
            print(f"  0.35 from box_top: {from_box:.5f}")
            print(f"  0.35 if measured from seg max_high: {from_seg:.5f}")
            print(f"  delta (box vs wick-high basis): {from_box - from_seg:.5f}")
        else:
            from_box = bb + rng * fib
            from_seg = min_l + (bt - min_l) * fib
            print(f"  0.35 from box_bottom: {from_box:.5f}")
            print(f"  0.35 if measured from seg min_low: {from_seg:.5f}")
            print(f"  delta (box vs wick-low basis): {from_box - from_seg:.5f}")

        # Wick above box_top after wave confirm?
        birth = eng.wave_birth_by_time.get(wt)
        if birth is not None and birth + 1 < len(df):
            post = df.iloc[int(birth) + 1 : min(dr + 5, len(df))]
            if not post.empty and wdir == 1:
                wick_mask = (post["high"].astype(float) > bt) & (
                    post["close"].astype(float) <= bt
                )
                if wick_mask.any():
                    wh = float(post.loc[wick_mask, "high"].max())
                    wt_time = post.loc[post["high"].astype(float).idxmax(), "time"]
                    wick_bos = wh - (wh - bb) * fib
                    print(f"  post-birth wick high={wh:.5f} at {wt_time}")
                    print(f"  0.35 from wick high: {wick_bos:.5f}")


if __name__ == "__main__":
    main()
