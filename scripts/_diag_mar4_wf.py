"""Check BOS attribution around Mar 4 WF scenario."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backtest.engine import BacktestEngine
from config.bot_config import LIVE_BOT_CONFIG
from strategy.trend_bos import compute_bos_wave_flip_map
from strategy.wave_sequence import compute_wave_sequence_info_per_wave

cfg = LIVE_BOT_CONFIG
df = pd.read_csv(ROOT / "data" / "EURUSD.x_M30.csv", parse_dates=["datetime"]).rename(
    columns={"datetime": "time"}
)
df = df[(df["time"] >= "2026-03-03") & (df["time"] <= "2026-03-06")].reset_index(drop=True)

eng = BacktestEngine(cfg)
eng.run(df, retain_wave_snapshot=True)

birth = eng.wave_birth_by_time
times = eng._run_df["time"]

print("WF waves:")
for w in eng.last_waves:
    if w.get("wave_origin") == "wf_continuation" or w.get("wf_wave_position"):
        wt = str(w["wave_time"])
        b = birth.get(wt)
        print(f"  {wt} dir={w['dir']} birth={b} time={times.iloc[b] if b is not None else '?'}")

print("\nBOS flip map in window:")
flip = compute_bos_wave_flip_map(df, eng.last_waves, cfg, wave_birth_bars=birth)
for bar, wt in sorted(flip.items()):
    print(f"  bar {bar} {times.iloc[bar]} -> {wt}")

print("\n_engine _bos_wave_times:", eng._bos_wave_times)
print("_bos_flip_wave_by_bar:")
for bar, w in sorted(eng._bos_flip_wave_by_bar.items()):
    print(f"  bar {bar} {times.iloc[bar]} -> {w['wave_time']} dir={w['dir']}")

seq = compute_wave_sequence_info_per_wave(df, eng.last_waves, cfg)
print("\nWaves Mar 3-5:")
for w in sorted(eng.last_waves, key=lambda x: birth.get(str(x["wave_time"]), 0)):
    wt = str(w["wave_time"])
    b = birth.get(wt)
    if b is None or b >= len(times):
        continue
    t = times.iloc[b]
    if t < pd.Timestamp("2026-03-03") or t > pd.Timestamp("2026-03-06"):
        continue
    info = seq.get(wt)
    idx = getattr(info, "index_in_trend", None) if info else None
    flags = []
    if wt in eng._bos_wave_times:
        flags.append("BOS_MAP")
    if w.get("is_bos_wave"):
        flags.append("is_bos_wave")
    if w.get("wave_origin") == "wf_continuation":
        flags.append("WF")
    print(f"  {wt} {t} dir={w['dir']} idx={idx} {' '.join(flags)}")

# per-bar last_up at key bars
if eng.trend_states_per_bar:
    for bar in [46, 47, 66, 79, 122, 123]:
        if bar < len(eng.trend_states_per_bar):
            st = eng.trend_states_per_bar[bar]
            print(f"\nbar {bar} {times.iloc[bar]}: dir={st.direction} last_up={st.last_up_wave_time} last_down={st.last_down_wave_time}")
