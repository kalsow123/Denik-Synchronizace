"""Full-run diagnostic: WF vs BOS flip map."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backtest.engine import BacktestEngine
from config.bot_config import LIVE_BOT_CONFIG
from strategy.wave_sequence import compute_wave_sequence_info_per_wave

cfg = LIVE_BOT_CONFIG
df = pd.read_csv(ROOT / "data" / "EURUSD.x_M30.csv", parse_dates=["datetime"]).rename(
    columns={"datetime": "time"}
)
df = df[(df["time"] >= "2026-03-03") & (df["time"] <= "2026-05-10")].reset_index(drop=True)

eng = BacktestEngine(cfg)
eng.run(df, retain_wave_snapshot=True)

birth = eng.wave_birth_by_time
times = df["time"]
seq = compute_wave_sequence_info_per_wave(df, eng.last_waves, cfg)

for title, lo, hi in [("Mar 4 WF", 60, 85), ("Apr30 WF", 2035, 2070), ("May6", 2210, 2250)]:
    print(f"\n=== {title} ===")
    for w in sorted(eng.last_waves, key=lambda x: birth.get(str(x["wave_time"]), 0)):
        wt = str(w["wave_time"])
        b = birth.get(wt)
        if b is None or not (lo <= b <= hi):
            continue
        info = seq.get(wt)
        idx = getattr(info, "index_in_trend", None) if info else None
        tags = []
        if wt in eng._bos_wave_times:
            tags.append("BOS_MAP")
        if w.get("wave_origin") == "wf_continuation":
            tags.append("WF")
        print(
            f"bar={b} {times.iloc[b]} wt={wt} dir={w['dir']} dl={w.get('draw_left')} dr={w.get('draw_right')} "
            f"idx={idx} {' '.join(tags)}"
        )

print("\n_bos_flip_wave_by_bar (selected bars):")
for bar, w in sorted(eng._bos_flip_wave_by_bar.items()):
    if bar in (47, 67, 79, 2047, 2051, 2057, 2110, 2219, 2238):
        print(f"  bar {bar} {times.iloc[bar]} -> {w['wave_time']} dir={w['dir']}")
