"""Proč BOS swing zůstane na WAVE 2 a kdy se posune na WAVE 3."""
from __future__ import annotations

import pandas as pd

from backtest.grid.backtest_conf import generate_combinations, get_profile
from backtest.grid.translator import grid_dict_to_bot_config
from strategy.trend_bos import (
    TrendState,
    _bos_close_flip_with_forgive,
    _maybe_seed_state_from_ext_post_trend,
    _wave_passes_hh_hl_structure_live,
    compute_bos_wave_flip_map,
    maybe_update_trend_state_with_wave,
    should_update_trend_state_for_wave,
)
from strategy.wave_detection_pine import run_pine_wave_simulation


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

    w2, w3 = by_wt["202505221300"], by_wt["202505222000"]
    print("=== Bear WAVE 2 / 3 ===")
    print(
        "W2",
        "top",
        w2["box_top"],
        "bot",
        w2["box_bottom"],
        "hh_hl_pass",
        w2.get("hh_hl_pass"),
        "birth",
        birth["202505221300"],
    )
    print(
        "W3",
        "top",
        w3["box_top"],
        "bot",
        w3["box_bottom"],
        "hh_hl_pass",
        w3.get("hh_hl_pass"),
        "birth",
        birth["202505222000"],
    )

    waves_by_birth: dict[int, list] = {}
    for w in waves:
        b = birth.get(w["wave_time"])
        if b is not None:
            waves_by_birth.setdefault(int(b), []).append(w)

    state = TrendState()
    closes = df["close"].astype(float).to_numpy()
    t0 = pd.Timestamp("2025-05-22 12:00:00")
    t1 = pd.Timestamp("2025-05-23 10:00:00")

    print("\n=== birth_bar timeline (compute_bos_wave_flip_map order) ===")
    for i in range(len(df)):
        t = df.iloc[i]["time"]
        if t < t0 or t > t1:
            bar_close = float(closes[i])
            flipped, state = _bos_close_flip_with_forgive(state, bar_close)
            for w in waves_by_birth.get(i, []):
                state = _maybe_seed_state_from_ext_post_trend(state, w)
                maybe_update_trend_state_with_wave(state, w, cfg)
            continue

        bar_close = float(closes[i])
        prev_ldt = state.last_down_box_top
        prev_dir = state.direction
        flipped, state = _bos_close_flip_with_forgive(state, bar_close)
        if flipped != 0:
            print(
                f"FLIP bar {t} close={bar_close:.5f} to={state.direction} "
                f"swing_used={prev_ldt} (pred maybe_update na tomto baru)"
            )
        for w in waves_by_birth.get(i, []):
            wt = str(w["wave_time"])
            if wt not in ("202505221300", "202505222000"):
                state = _maybe_seed_state_from_ext_post_trend(state, w)
                maybe_update_trend_state_with_wave(state, w, cfg)
                continue
            upd = should_update_trend_state_for_wave(state, w, cfg)
            hh = _wave_passes_hh_hl_structure_live(state, w)
            print(
                f"  birth {t} {wt} should_update_swing={upd} hh_hl_live={hh} "
                f"ldt_before={prev_ldt}"
            )
            state = _maybe_seed_state_from_ext_post_trend(state, w)
            maybe_update_trend_state_with_wave(state, w, cfg)
            print(f"    -> ldt_after={state.last_down_box_top} dir={state.direction}")

    flip_map = compute_bos_wave_flip_map(df, waves, cfg, wave_birth_bars=birth)
    print("\n=== flip_map May22-23 ===")
    for bar_ix, wt in sorted(flip_map.items()):
        t = df.iloc[bar_ix]["time"]
        if t < t0 or t > t1:
            continue
        w = by_wt[wt]
        print(
            "bar",
            t,
            "bos_wave",
            wt,
            "dir",
            w["dir"],
            "box_top",
            w["box_top"],
            "close",
            df.iloc[bar_ix]["close"],
        )

    # Bull waves HH/HL after flip
    print("\n=== Bull vlny po bear sekvenci ===")
    for wt in ("202505230430", "202505231430"):
        w = by_wt[wt]
        print(wt, "hh_hl_pass", w.get("hh_hl_pass"), "top", w["box_top"], "bot", w["box_bottom"])


if __name__ == "__main__":
    main()
