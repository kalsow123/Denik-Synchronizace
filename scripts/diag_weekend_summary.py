"""Summary kazdeho vikendoveho gapu: pivot anchor + EXT klasifikace + cenovy rozsah."""
from __future__ import annotations

import pandas as pd

from config.bot_config import BotConfig
from strategy.wave_detection_pine import (
    _compute_after_data_gap_mask,
    run_pine_wave_simulation,
)


def main() -> None:
    cfg = BotConfig(
        wave_min_pct=0.26,
        min_opp_bars=3,
        entry_fib_level=0.5,
        sl_fib_level=0.8,
        rrr=2.0,
        wave_plus=True,
        ext_enabled=True,
        ext_wave_min_pct=0.76,
        ext_weekend_gap_relax_factor=0.5,
    )
    df = pd.read_csv("data/EURUSD_M30.csv", parse_dates=["datetime"]).rename(
        columns={"datetime": "time"}
    )
    full = df[(df["time"] >= "2026-03-03") & (df["time"] <= "2026-05-10")].reset_index(drop=True)
    waves, _, _, _ = run_pine_wave_simulation(full, cfg)
    gm = _compute_after_data_gap_mask(full["time"])
    gaps = [i for i, v in enumerate(gm) if v]

    print(f"{'GapBar':>8} {'GapTime':<22} {'PrevC':>8} {'CurO':>8} {'Jump':>8}  "
          f"{'WaveDir':>4} {'PivotBar':>8} {'PivotTime':<22} {'PivotPx':>8} {'BotPx':>8} "
          f"{'Move%':>6} {'EXT':<5}")
    for gi in gaps:
        prev = full.iloc[gi - 1]
        cur = full.iloc[gi]
        jump = float(cur["open"]) - float(prev["close"])
        spans = [w for w in waves if int(w["draw_left"]) <= gi <= int(w["draw_right"])]
        if not spans:
            print(f"{gi:>8} {str(prev['time'])[:19]:<22} -- NO WAVE SPANS GAP --")
            continue
        # Take widest wave
        w = max(spans, key=lambda x: float(x["move_pct"]))
        L = int(w["draw_left"])
        wd = int(w["dir"])
        top = float(w["box_top"])
        bot = float(w["box_bottom"])
        if wd == -1:
            pivot_px = top
            cand_px = bot
        else:
            pivot_px = bot
            cand_px = top
        print(
            f"{gi:>8} {str(prev['time'])[:19]:<22} "
            f"{prev['close']:.5f} {cur['open']:.5f} {jump:+.5f}  "
            f"{wd:+d}    {L:>8} {str(full['time'].iloc[L])[:19]:<22} "
            f"{pivot_px:.5f} {cand_px:.5f} "
            f"{float(w['move_pct']):>6.3f} {str(bool(w.get('is_ext', False))):<5}"
        )

    print("\nDOWN gaps (jump < -0.0015):")
    for gi in gaps:
        prev = full.iloc[gi - 1]
        cur = full.iloc[gi]
        jump = float(cur["open"]) - float(prev["close"])
        if jump > -0.0015:
            continue
        # What was Friday's high (last 8 bars before gap)?
        fri = full.iloc[max(0, gi - 16):gi]
        fri_high = float(fri["high"].max())
        # What was Monday's low (next 32 bars after gap)?
        mon = full.iloc[gi:min(len(full), gi + 32)]
        mon_low = float(mon["low"].min())
        ext_pct = (fri_high - mon_low) / fri_high * 100
        print(
            f"  gap@{gi} {str(prev['time'])[:19]} jump={jump:+.5f}  "
            f"fri_high={fri_high:.5f}  mon_low(32bars)={mon_low:.5f}  "
            f"true_move={ext_pct:.3f}%"
        )


if __name__ == "__main__":
    main()
