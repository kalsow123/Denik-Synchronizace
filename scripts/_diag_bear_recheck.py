"""Re-analyza bear vlny po BOS flipu na bull (Jun 5)."""
from __future__ import annotations

import pandas as pd

from backtest.engine import BacktestEngine
from backtest.grid.backtest_conf import generate_combinations, get_profile
from backtest.grid.translator import grid_dict_to_bot_config
from strategy.trend_bos import (
    collect_bos_flip_events,
    filter_waves_for_structure_display,
    tag_waves_hh_hl_pass,
)


def main() -> None:
    c = next(
        x
        for x in generate_combinations(get_profile("testing"))
        if x.get("bos_entry_enable") and not x.get("wave_counter_two_sided_enabled")
    )
    cfg = grid_dict_to_bot_config(c)
    df_full = pd.read_csv("data/EURUSD_M30.csv", parse_dates=["datetime"]).rename(
        columns={"datetime": "time"}
    )
    df_full = df_full[
        (df_full["time"] >= "2025-05-10") & (df_full["time"] <= "2025-06-15")
    ].reset_index(drop=True)
    eng = BacktestEngine(cfg)
    eng.run(df_full)
    df = df_full[
        (df_full["time"] >= "2025-06-03") & (df_full["time"] <= "2025-06-07")
    ]

    print("=== BOS flipy (close-based) 3.-7.6. ===")
    for ev in collect_bos_flip_events(df_full, eng._all_waves, cfg):
        t_flip, swing, label, t0 = ev
        if pd.Timestamp(t_flip) < pd.Timestamp("2025-06-01"):
            continue
        if pd.Timestamp(t_flip) > pd.Timestamp("2025-06-08"):
            continue
        safe = str(label).encode("ascii", "replace").decode("ascii")
        print(f"  {t_flip} | {safe} | swing={swing:.5f}")

    print("\n=== Vlny s index_in_trend 3.-7.6. ===")
    for wt in sorted(eng.waves_by_wave_time, key=lambda x: eng.wave_birth_by_time.get(x, 10**9)):
        w = eng.waves_by_wave_time[wt]
        idx = w.get("index_in_trend")
        b = eng.wave_birth_by_time.get(wt)
        if b is None:
            continue
        t = df_full.iloc[int(b)]["time"]
        if t < pd.Timestamp("2025-06-03") or t > pd.Timestamp("2025-06-07"):
            continue
        if idx is not None or wt == "202506050830":
            print(
                f"  {t} {wt} dir={w['dir']:+d} idx={idx} hh_hl={w.get('hh_hl_pass')} "
                f"is_ext={w.get('is_ext')} move={float(w.get('move_pct',0)):.2f}% "
                f"dr={df_full.iloc[int(w['draw_right'])]['time']}"
            )

    print("\n=== Trend direction po BOS bull flipu (prvni Jun4+) ===")
    bull_flip_bars = []
    for j in range(1, len(df_full)):
        prev = eng.trend_states_per_bar[j - 1].direction
        cur = eng.trend_states_per_bar[j].direction
        if prev == "bear" and cur == "bull" and j in eng._close_bos_flip_bar_indices:
            bull_flip_bars.append(j)
    if bull_flip_bars:
        bf = bull_flip_bars[0]
        print(f"Prvni bear->bull flip: {df_full.iloc[bf]['time']} bar {bf}")
        for j in range(bf, min(bf + 120, len(df_full))):
            ts = eng.trend_states_per_bar[j]
            prev = eng.trend_states_per_bar[j - 1]
            if ts.direction != prev.direction or j in eng._close_bos_flip_bar_indices:
                print(
                    f"  {df_full.iloc[j]['time']} {prev.direction}->{ts.direction} "
                    f"FLIP={j in eng._close_bos_flip_bar_indices} "
                    f"last_up={ts.last_up_wave_time} last_down={ts.last_down_wave_time}"
                )

    bear = eng.waves_by_wave_time.get("202506050830")
    if bear:
        bi = int(eng.wave_birth_by_time.get("202506050830", bear["draw_right"]))
        print(f"\n=== Bear 202506050830 ===")
        print(f"birth {df_full.iloc[bi]['time']} dr {df_full.iloc[int(bear['draw_right'])]['time']}")
        bi1600 = int(df_full.index[df_full["time"] == pd.Timestamp("2025-06-05 16:00:00")][0])
        print(f"bos_wave_at_1600={eng._bos_flip_wave_by_bar.get(bi1600, {}).get('wave_time')}")
        tag_waves_hh_hl_pass(df_full, list(eng._all_waves), cfg)
        kept = filter_waves_for_structure_display(df_full, eng._all_waves, cfg)
        print("in_visual=", "202506050830" in {w["wave_time"] for w in kept})
        print("in_bos_map=", "202506050830" in eng._bos_wave_times)


if __name__ == "__main__":
    main()
