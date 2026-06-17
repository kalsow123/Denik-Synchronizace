"""Diag: EXT2 UP -> proc BEAR idx=2 misto 1 (May 14-20)."""
from __future__ import annotations

import pandas as pd

from backtest.engine import BacktestEngine
from backtest.grid.backtest_conf import generate_combinations, get_profile
from backtest.grid.translator import grid_dict_to_bot_config


def main() -> None:
    cfg = grid_dict_to_bot_config(generate_combinations(get_profile("testing"))[0])
    df = pd.read_csv("data/EURUSD.x_M30.csv", parse_dates=["datetime"]).rename(
        columns={"datetime": "time"}
    )
    df = df[(df["time"] >= "2025-05-12") & (df["time"] <= "2025-05-22")].reset_index(
        drop=True
    )
    eng = BacktestEngine(cfg)
    eng.run(df.copy(), retain_wave_snapshot=True)

    t0, t1 = pd.Timestamp("2025-05-14"), pd.Timestamp("2025-05-20")
    print("=== WAVES May14-20 ===")
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
        if w.get("ext_post_trend_seed_dir"):
            flags.append(f"seed={w.get('ext_post_trend_seed_dir')}")
        if wt in (eng._bos_wave_times or set()):
            flags.append("bos_wt")
        print(
            f"{bt} {d:2} idx={idx} bos={bos} wt={wt} "
            f"low={w.get('box_bottom')} {' '.join(flags)}"
        )


if __name__ == "__main__":
    main()
