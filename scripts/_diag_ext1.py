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
df = pd.read_csv(ROOT / "data" / "EURUSD.x_M30.csv", parse_dates=["datetime"]).rename(columns={"datetime": "time"})
df = df[(df["time"] >= "2026-03-03") & (df["time"] <= "2026-05-10")].reset_index(drop=True)
eng = BacktestEngine(cfg)
eng.run(df, retain_wave_snapshot=True)
times = df["time"]
seq = compute_wave_sequence_info_per_wave(df, eng.last_waves, cfg)
vis = {str(w.get("wave_time")) for w in eng.last_waves_for_visual}

def dump(t_lo, t_hi):
    rows = []
    for w in eng.last_waves:
        dr = w.get("draw_right")
        if dr is None or not (0 <= int(dr) < len(df)):
            continue
        t = times.iloc[int(dr)]
        if not (t_lo <= t <= t_hi):
            continue
        info = seq.get(str(w["wave_time"]))
        idx = getattr(info, "index_in_trend", None) if info else None
        rows.append((int(dr), t, w.get("dir"), w.get("is_ext"), idx, w.get("hh_hl_pass"),
                     str(w["wave_time"]) in vis))
    rows.sort(key=lambda r: r[0])
    for dr, t, d, ext, idx, hh, drawn in rows:
        dc = "UP" if d == 1 else "DN"
        fl = ("EXT " if ext else "") + ("hhX" if hh is False else "")
        print(f"  dr={dr:4d} {t:%m-%d %H:%M} {dc} idx={str(idx):>4} {'DRAWN' if drawn else 'ghost'} {fl}")

print("=== Mar 11-17 (EXT 6 -> BOS -> next down) ===")
dump(pd.Timestamp("2026-03-11 00:00"), pd.Timestamp("2026-03-17 12:00"))
print("=== Mar 17-24 ===")
dump(pd.Timestamp("2026-03-17 12:00"), pd.Timestamp("2026-03-24 00:00"))
print("=== Mar 25-30 (bear) ===")
dump(pd.Timestamp("2026-03-25 00:00"), pd.Timestamp("2026-03-30 23:00"))
