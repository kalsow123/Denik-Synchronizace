from __future__ import annotations

import pandas as pd

from backtest.engine import BacktestEngine
from backtest.grid.backtest_conf import generate_combinations, get_profile
from backtest.grid.translator import grid_dict_to_bot_config

cfg = grid_dict_to_bot_config(generate_combinations(get_profile("testing"))[0])
df = pd.read_csv("data/EURUSD.x_M30.csv", parse_dates=["datetime"]).rename(
    columns={"datetime": "time"}
)
df = df[(df["time"] >= "2025-05-12") & (df["time"] <= "2025-05-13")].reset_index(drop=True)
eng = BacktestEngine(cfg)
eng.run(df.copy(), retain_wave_snapshot=True)
for w in sorted(
    eng.last_waves,
    key=lambda x: (int(x.get("draw_right", 0)), str(x.get("wave_time", ""))),
):
    wt = str(w["wave_time"])
    dr = int(w.get("draw_right", 0))
    if dr >= len(df):
        continue
    bt = df.iloc[dr]["time"]
    info = eng.wave_sequence_info.get(wt)
    idx = info.index_in_trend if info else None
    d = "UP" if int(w.get("dir", 0)) == 1 else "DN"
    flags = []
    if w.get("is_ext"):
        flags.append("EXT")
    if w.get("in_ext_range"):
        flags.append("in_ext")
    print(f"{bt} {d} idx={idx} {' '.join(flags)} wt={wt}")
