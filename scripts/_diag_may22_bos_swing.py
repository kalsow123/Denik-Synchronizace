"""BOS swing level: bear WAVE 2 vs 3 high (graf + pocitani + entry)."""
from __future__ import annotations

import pandas as pd

from backtest.grid.backtest_conf import generate_combinations, get_profile
from backtest.grid.translator import grid_dict_to_bot_config
from strategy.trend_bos import (
    _bos_close_flip_with_forgive,
    compute_bos_wave_flip_map,
    maybe_update_trend_state_with_wave,
    TrendState,
)
from strategy.wave_detection_pine import run_pine_wave_simulation
from strategy.wave_sequence import compute_wave_sequence_info_per_wave


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
    df = df[(df["time"] >= "2025-05-10") & (df["time"] <= "2025-05-26")].reset_index(
        drop=True
    )
    waves, birth, _, _ = run_pine_wave_simulation(df, cfg)
    by_wt = {str(w["wave_time"]): w for w in waves}
    seq = compute_wave_sequence_info_per_wave(df, waves, cfg)

    bear2 = "202505221300"
    bear3 = "202505222000"
    up_ghost = "202505222230"
    up_first = "202505230430"
    up_bos = "202505231430"

    print("=== Bear WAVE 2 / 3 box highs ===")
    for t in (bear2, bear3):
        w = by_wt[t]
        info = seq.get(t)
        print(
            t,
            "idx",
            info.index_in_trend if info else None,
            "box_top",
            w["box_top"],
            "draw_right",
            w["draw_right"],
            "bar",
            df.iloc[int(w["draw_right"])]["time"],
            "birth",
            birth.get(t),
        )

    flip_map = compute_bos_wave_flip_map(df, waves, cfg, wave_birth_bars=birth)
    print("\n=== compute_bos_wave_flip_map (graf + BOS entry) ===")
    for bar_ix, wt in sorted(flip_map.items()):
        t = df.iloc[bar_ix]["time"]
        if t < pd.Timestamp("2025-05-21") or t > pd.Timestamp("2025-05-24"):
            continue
        w = by_wt[wt]
        print(
            "flip",
            t,
            "bar_ix",
            bar_ix,
            "bos_wave_time",
            wt,
            "dir",
            w["dir"],
            "box_top",
            w.get("box_top"),
            "box_bottom",
            w.get("box_bottom"),
        )

    bos_i = int(df.index[df["time"] == pd.Timestamp("2025-05-22 15:00:00")][0])
    bos_close = float(df.iloc[bos_i]["close"])
    print(f"\n=== Swing replay (birth_bar) do BOS baru {df.iloc[bos_i]['time']} ===")
    print("close na BOS baru:", bos_close)

    state = TrendState()
    closes = df["close"].astype(float).to_numpy()
    waves_by_birth: dict[int, list] = {}
    for w in waves:
        b = birth.get(w["wave_time"])
        if b is not None:
            waves_by_birth.setdefault(int(b), []).append(w)

    last_ldt_wave: str | None = None
    for i in range(bos_i + 1):
        bar_close = float(closes[i])
        flipped, state = _bos_close_flip_with_forgive(state, bar_close)
        if flipped != 0 and i >= bos_i - 5:
            print(
                "  FLIP bar",
                df.iloc[i]["time"],
                "to",
                state.direction,
                "ldt",
                state.last_down_box_top,
            )
        for w in waves_by_birth.get(i, []):
            prev_ldt = state.last_down_box_top
            state = maybe_update_trend_state_with_wave(state, w, cfg)
            if int(w["dir"]) == -1 and state.last_down_box_top != prev_ldt:
                last_ldt_wave = str(w["wave_time"])
                if i >= 380:
                    print(
                        "  DN birth",
                        df.iloc[i]["time"],
                        w["wave_time"],
                        "ldt",
                        state.last_down_box_top,
                        "box_top",
                        w["box_top"],
                    )

    print("\nSwing level (last_down_box_top) pri BOS:", state.last_down_box_top)
    print("Posledni DN vlna ktera nastavila ldt:", last_ldt_wave)
    for label, t in [("bear2", bear2), ("bear3", bear3)]:
        top = float(by_wt[t]["box_top"])
        print(f"  {label} box_top={top} match={abs(top - float(state.last_down_box_top or 0)) < 1e-6}")

    print("\n=== wave_sequence (pocitani vln, draw_right) ===")
    for t in (bear2, bear3, up_ghost, up_first, up_bos):
        w = by_wt.get(t)
        if not w:
            continue
        info = seq.get(t)
        print(
            t,
            "UP" if w["dir"] == 1 else "DN",
            "idx",
            info.index_in_trend if info else None,
            "is_bos_wave",
            info.is_bos_wave if info else False,
            "hh",
            w.get("hh_hl_pass"),
        )


if __name__ == "__main__":
    main()
