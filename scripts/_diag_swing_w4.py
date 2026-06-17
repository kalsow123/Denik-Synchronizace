"""Does WAVE4 go through climax block without swing update?"""
from __future__ import annotations

import pandas as pd

from backtest.engine import BacktestEngine
from backtest.grid.backtest_conf import generate_combinations, get_profile
from backtest.grid.translator import grid_dict_to_bot_config
from strategy.trend_bos import (
    TrendState,
    compute_trend_states_per_bar,
    compute_wave_birth_bars_pine,
    maybe_update_trend_state_with_wave,
    should_update_trend_state_for_wave,
)
from strategy.ext_range import reapply_ext_range_tags
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

    w4 = next(w for w in waves if w["wave_time"] == "202505211700")
    print("WAVE4:", {k: w4.get(k) for k in ["box_bottom", "box_top", "in_ext_range", "ext_post_range_terminator", "is_ext"]})
    print("should_update:", should_update_trend_state_for_wave(TrendState(direction="bull"), w4, cfg))

    states = compute_trend_states_per_bar(df, waves, cfg)
    for bar in [369, 370, 371, 384, 407]:
        s = states[bar]
        print(f"bar {bar} {df.iloc[bar]['time']} dir={s.direction} lub={s.last_up_box_bottom} ldt={s.last_down_box_top}")


if __name__ == "__main__":
    main()
