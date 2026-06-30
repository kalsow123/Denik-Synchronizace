"""Proč wave_bos 12.5. 10:00 — read-only."""
from __future__ import annotations

import pandas as pd

from backtest.engine import BacktestEngine
from backtest.grid.backtest_conf import generate_combinations, get_profile
from backtest.grid.translator import grid_dict_to_bot_config


def main() -> None:
    cfg = grid_dict_to_bot_config(
        next(
            c
            for c in generate_combinations(get_profile("testing"))
            if c.get("trend_hh_hl_filter_enabled")
            and not c.get("wave_counter_two_sided_enabled")
        )
    )
    df = pd.read_csv("data/EURUSD_M30.csv", parse_dates=["datetime"]).rename(
        columns={"datetime": "time"}
    )
    df = df[(df["time"] >= "2025-05-12") & (df["time"] <= "2025-05-13")].reset_index(
        drop=True
    )
    eng = BacktestEngine(cfg)
    eng.run(df, retain_wave_snapshot=True)

    w = next(x for x in eng._all_waves if str(x["wave_time"]) == "202505120400")
    dl = int(w.get("draw_left", 0))
    print("UP WAVE pred BOS (202505120400):")
    print(f"  cas extremu: {df.iloc[int(w['draw_right'])]['time']}")
    print(f"  box_bottom (swing low UP vlny): {w.get('box_bottom')}")
    print(f"  box_top: {w.get('box_top')}")
    print(f"  draw_left (zacatek boxu na grafu): {df.iloc[dl]['time'] if dl < len(df) else dl}")

    print("\nBary kolem BOS flipu:")
    for i, row in df.iterrows():
        t = row["time"]
        if t < pd.Timestamp("2025-05-12 09:00") or t > pd.Timestamp("2025-05-12 11:00"):
            continue
        bb = float(w.get("box_bottom", 0))
        close = float(row["close"])
        broke = close < bb
        print(
            f"  {t} close={close:.5f}  "
            f"{'PRORAZENI swing low -> wave_bos bear' if broke else 'jeste nad swingem'}"
        )

    print("\nBOS cara na grafu:")
    for ev in eng.bos_flip_events or []:
        print(f"  flip cas: {ev[0]}")
        print(f"  swing level (UP low): {ev[1]}")
        print(f"  label: {ev[2]}")
        print(f"  segment od: {ev[3]}")


if __name__ == "__main__":
    main()
