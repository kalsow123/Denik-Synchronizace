"""Rychla diagnostika: birth_by_time vs draw_right pro TP-vlny (kvuli sync pre-marking)."""
from __future__ import annotations
import sys, types
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# fake mt5
fake = types.SimpleNamespace()
for k, v in {"TIMEFRAME_M1":1,"TIMEFRAME_M3":3,"TIMEFRAME_M5":5,"TIMEFRAME_M15":15,
             "TIMEFRAME_M30":30,"TIMEFRAME_H1":16385,"TIMEFRAME_H4":16388,
             "TIMEFRAME_D1":16408,"TIMEFRAME_W1":32769}.items():
    setattr(fake, k, v)
fake.positions_get = lambda *a, **k: ()
fake.orders_get = lambda *a, **k: ()
fake.symbol_info = lambda *a, **k: types.SimpleNamespace(digits=5, point=1e-5, trade_contract_size=100000, visible=True)
fake.symbol_info_tick = lambda *a, **k: types.SimpleNamespace(ask=1.1, bid=1.1, last=1.1, time=0)
fake.initialize = lambda *a, **k: True
fake.last_error = lambda: (0, "ok")
sys.modules["MetaTrader5"] = fake

from backtest.data_loader import filter_by_date_range, load_csv
from config.bot_config import LIVE_BOT_CONFIG
from config.position_modes import resolve_grid_engine_config
from runtime.live_wave_isolation import resolve_live_execution_config
from strategy.wave_detection import detect_waves
from strategy.wave_detection_pine import compute_wave_birth_bars_pine
from strategy.wave_sequence import sync_wave_sequence_state, is_tp_wave_index
from strategy.ext_range import ext_range_enabled, reapply_ext_range_tags
from runtime.ext_live import ExtLiveRuntime

cfg = resolve_live_execution_config(LIVE_BOT_CONFIG)
df = filter_by_date_range(load_csv(str(ROOT / "data" / "EURUSD_M30.csv")), "2025-11-10", "2026-05-09").reset_index(drop=True)
waves = detect_waves(df, cfg)
wave_birth = compute_wave_birth_bars_pine(df, cfg)
seq_info, protected = sync_wave_sequence_state(df, waves, cfg)
if ext_range_enabled(cfg):
    reapply_ext_range_tags(waves, cfg, df=df, wave_birth=wave_birth)
    seq_info, protected = sync_wave_sequence_state(df, waves, cfg)

ext_rt = ExtLiveRuntime()
ext_rt.sync_from_mt5(cfg)
ext_rt.refresh_simulation(df, cfg, seq_info=seq_info, protected_waves=protected, waves=waves)
ext_birth = ext_rt._wave_birth_by_time

target_n = int(getattr(cfg, "tp_target_wave_index", 0) or 0)
print("target_n =", target_n, " pocet vln =", len(waves))
print(f"{'wave_time':<14}{'idx':>4}{'draw_right':>11}{'birth_pine':>11}{'birth_ext':>11}  flag")
n_tp = 0
bad_pine = bad_ext = 0
for w in waves:
    wt = str(w["wave_time"])
    info = seq_info.get(wt)
    if info is None or info.index_in_trend is None:
        continue
    idx = int(info.index_in_trend)
    if not is_tp_wave_index(idx, target_n):
        continue
    n_tp += 1
    dr = int(w.get("draw_right", -1))
    bp = wave_birth.get(wt)
    be = ext_birth.get(wt)
    # event vystreli na draw_right; sync predznaci pokud birth < draw_right
    fp = "PINE_skip" if (bp is not None and bp < dr) else ""
    fe = "EXT_skip" if (be is not None and be < dr) else ""
    if fp: bad_pine += 1
    if fe: bad_ext += 1
    if n_tp <= 30 or fp or fe:
        print(f"{wt:<14}{idx:>4}{dr:>11}{str(bp):>11}{str(be):>11}  {fp} {fe}")
print(f"\nTP vln celkem: {n_tp}")
print(f"PINE birth < draw_right (event by se preskocil): {bad_pine}")
print(f"EXT  birth < draw_right (event by se preskocil): {bad_ext}")
print("maps shodne?", wave_birth == ext_birth)
