"""Check trend_established_by_ext at bar 407 in full wave_sequence loop."""
from __future__ import annotations

import pandas as pd

from backtest.grid.backtest_conf import generate_combinations, get_profile
from backtest.grid.translator import grid_dict_to_bot_config
from strategy.ext_range import reapply_ext_range_tags
from strategy.trend_bos import TrendState, maybe_update_trend_state_with_wave
from strategy.wave_detection_pine import run_pine_wave_simulation


def main() -> None:
    cfg = grid_dict_to_bot_config(generate_combinations(get_profile("testing"))[0])
    df = pd.read_csv("data/EURUSD.x_M30.csv", parse_dates=["datetime"]).rename(
        columns={"datetime": "time"}
    )
    df = df[(df["time"] >= "2025-05-10") & (df["time"] <= "2025-05-28")].reset_index(
        drop=True
    )
    waves, birth, _, _ = run_pine_wave_simulation(df, cfg)
    reapply_ext_range_tags(waves, cfg, df=df, wave_birth=birth)

    from strategy.wave_sequence import compute_wave_sequence_info_per_wave

    # Monkeypatch by importing module internals - instead run full and check via
    # inspecting what Mech C would see: replicate key state fields from seq output
    # Actually instrument by calling compute and checking BOS timeline separately

    from strategy.ext_range import check_close_breaks_ext_extreme, check_ext_bos_via_fib_35

    waves_by_extreme = {}
    for w in waves:
        dr = int(w["draw_right"])
        waves_by_extreme.setdefault(dr, []).append(w)

    state = TrendState()
    trend_established_by_ext = False
    ext_active_wave = None
    is_bos_wave_pending = False
    closes = df["close"].astype(float).to_numpy()
    n = len(df)

    # Import the actual loop helpers from wave_sequence module
    import strategy.wave_sequence as ws

    ext1_count_window = False
    ext1_protect_window = False
    ext1_counter_idx = 0
    counter_up = counter_down = 0
    ext_climax_reversal_dir = None
    climax_dir = climax_idx = climax_extreme = None

    for i in range(n):
        t = df.iloc[i]["time"]
        bar_close = float(closes[i])
        if t < pd.Timestamp("2025-05-21 09:00"):
            continue
        if t > pd.Timestamp("2025-05-22 12:00"):
            break

        mech_b_fired = False
        if ext_active_wave is not None:
            if check_close_breaks_ext_extreme(bar_close, ext_active_wave):
                ext_active_wave = None
            elif check_ext_bos_via_fib_35(bar_close, ext_active_wave):
                trend_established_by_ext = False
                mech_b_fired = True

        has_wave_dr = any(int(w.get("draw_right", -1)) == i for w in waves)
        mech_c_fired = False
        mech_c_forgave = False
        if not mech_b_fired and not has_wave_dr:
            if state.direction == "bull" and state.last_up_box_bottom is not None:
                if bar_close < state.last_up_box_bottom:
                    if trend_established_by_ext:
                        trend_established_by_ext = False
                        state.last_up_box_bottom = None
                        mech_c_forgave = True
                    else:
                        state.direction = "bear"
                        is_bos_wave_pending = True
                        state.last_up_box_bottom = None
                        state.last_down_box_top = None
                        mech_c_fired = True

        for w in waves_by_extreme.get(i, []):
            wt = w["wave_time"]
            wdir = int(w["dir"])
            is_ext = bool(w.get("is_ext"))

            if wt == "202505211700":
                # simulate KROK 3.2 terminator
                ext_active_wave = None
                trend_established_by_ext = False
                maybe_update_trend_state_with_wave(state, w, cfg)
                if w.get("ext_post_range_terminator"):
                    trend_established_by_ext = False
                continue
            if wt == "202505210930":
                ext_active_wave = w
                trend_established_by_ext = True  # scenario C EXT
                ext1_count_window = True
                maybe_update_trend_state_with_wave(state, w, cfg)
                continue
            if wt == "202505211130":
                maybe_update_trend_state_with_wave(state, w, cfg)
                continue

            maybe_update_trend_state_with_wave(state, w, cfg)

        if i >= 368 and (mech_c_fired or mech_c_forgave or waves_by_extreme.get(i)):
            print(
                f"{i} {t} c={bar_close:.5f} dir={state.direction} lub={state.last_up_box_bottom} "
                f"tebe={trend_established_by_ext} pending={is_bos_wave_pending} "
                f"mech_c={mech_c_fired} forgave={mech_c_forgave} ext_act={ext_active_wave is not None}"
            )


if __name__ == "__main__":
    main()
