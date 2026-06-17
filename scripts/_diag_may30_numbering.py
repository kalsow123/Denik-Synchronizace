"""Diagnostika May 29-30: proc EXT UP dostala idx=1 misto 3."""
import pandas as pd
from backtest.engine import BacktestEngine
from backtest.grid.backtest_conf import generate_combinations, get_profile
from backtest.grid.translator import grid_dict_to_bot_config

cfg = grid_dict_to_bot_config(list(generate_combinations(get_profile("testing")))[0])
df = pd.read_csv("data/EURUSD.x_M30.csv", parse_dates=["datetime"]).rename(
    columns={"datetime": "time"}
)
df = df[(df["time"] >= "2025-05-20") & (df["time"] <= "2025-06-02")].reset_index(drop=True)
eng = BacktestEngine(cfg)
eng.run(df, retain_wave_snapshot=True)

TARGET = "202505292100"
print("Kontext pred problematickou EXT UP:")
for ww in sorted(eng._all_waves, key=lambda x: x.get("draw_right", 0)):
    dr = ww.get("draw_right")
    if dr is None:
        continue
    ti = df.iloc[int(dr)]["time"]
    if ti < pd.Timestamp("2025-05-28") or ti > pd.Timestamp("2025-05-30"):
        continue
    t = ww["wave_time"]
    info = eng.wave_sequence_info.get(str(t))
    mark = " <<<" if t == TARGET else ""
    print(
        f"  {t} {ti} dir={ww.get('dir')} ext={ww.get('is_ext')}"
        f" idx={info.index_in_trend if info else None}"
        f" bos={info.is_bos_wave if info else None}{mark}"
    )

w = eng.waves_by_wave_time[TARGET]
print("\nProblematicka vlna:", TARGET)
print("  is_ext=", w.get("is_ext"), "dir=", w.get("dir"))
print("  move_pct=", w.get("move_pct"))
info = eng.wave_sequence_info[TARGET]
print("  seq:", info)
