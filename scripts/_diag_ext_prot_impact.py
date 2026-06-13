"""Diagnostika dopadu T_EXT_PROT na EURUSD M30 (LIVE_BOT_CONFIG)."""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backtest.engine import BacktestEngine
from config.bot_config import LIVE_BOT_CONFIG
from strategy.ext_logic import is_ext_counter_trade
from strategy.wave_sequence import should_close_trade_on_bos_flip

cfg = LIVE_BOT_CONFIG
df = pd.read_csv(ROOT / "data" / "EURUSD.x_M30.csv", parse_dates=["datetime"])
df = df.rename(columns={"datetime": "time"})
df = df[(df["time"] >= "2026-03-03") & (df["time"] <= "2026-05-10")].reset_index(drop=True)

flip_stats = {
    "flip_ext_counter_old_close": 0,
    "flip_ext_counter_new_close": 0,
    "flip_ext_counter_saved": 0,
}

_orig_bos = BacktestEngine._handle_bos_exit_on_bar


def _wrap_bos(self, *args, **kwargs):
    result = _orig_bos(self, *args, **kwargs)
    bar_idx = args[0]
    flipped, direction, _ = self._bos_flip_state_on_bar(bar_idx)
    if not flipped or direction == "neutral":
        return result
    broken_dir = -1 if direction == "bull" else +1
    for trade in list(self.open_trades):
        if not is_ext_counter_trade(trade):
            continue
        old_close = int(trade.dir) == int(broken_dir) or flipped
        new_close = should_close_trade_on_bos_flip(
            trade,
            broken_dir=broken_dir,
            flipped=flipped,
            protected_wave_times=set(),
        )
        if old_close:
            flip_stats["flip_ext_counter_old_close"] += 1
        if new_close:
            flip_stats["flip_ext_counter_new_close"] += 1
        if old_close and not new_close:
            flip_stats["flip_ext_counter_saved"] += 1
    return result


BacktestEngine._handle_bos_exit_on_bar = _wrap_bos
eng = BacktestEngine(cfg)
eng.run(df)
wd = eng.wave_debug

by_reason_ext = Counter()
by_reason_ext_counter = Counter()
for ct in eng.closed_trades:
    if not ct.is_ext:
        continue
    by_reason_ext[ct.close_reason] += 1
    if ct.entry_tag in ("ext_counter_bos", "ext_counter_time"):
        by_reason_ext_counter[ct.close_reason] += 1

print("Dataset: EURUSD M30, 2026-03-03 .. 2026-05-10, LIVE_BOT_CONFIG")
print()
print("=== Ochrana parent EXT vlna (wave_debug) ===")
print(f"  TP_WAVE_N:  ext_protected_on_parent_wave     = {wd.get('ext_protected_on_parent_wave', 0)}")
print(f"  BOS flip:   ext_protected_on_parent_wave_bos = {wd.get('ext_protected_on_parent_wave_bos', 0)}")
print()
print("=== BOS flip — EXT counter (stara vs nova logika pri flipu) ===")
print(f"  Stara logika by zavrela: {flip_stats['flip_ext_counter_old_close']}")
print(f"  Nova logika zavre:      {flip_stats['flip_ext_counter_new_close']}")
print(f"  Rozdil (usetreno):      {flip_stats['flip_ext_counter_saved']}")
print()
print("=== BOS exit celkem ===")
print(f"  bos_exit_trades_closed: {wd.get('bos_exit_trades_closed', 0)}")
print(f"  bos_exit_sl_protected:  {wd.get('bos_exit_sl_protected', 0)}")
print()
print("=== EXT uzavreni podle close_reason ===")
for r, n in sorted(by_reason_ext.items()):
    print(f"  {r}: {n}")
print("--- EXT counter ---")
for r, n in sorted(by_reason_ext_counter.items()):
    print(f"  {r}: {n}")
print()
print("=== EXT eventy ===")
for k in (
    "ext_secondary_placed",
    "ext_counter_time_placed",
    "ext_counter_bos_placed",
    "ext_bos_triggered",
    "ext_bos_trend_closed",
    "ext_counter_new_trend_closed",
    "tp_wave_events_fired",
    "tp_wave_positions_closed",
):
    print(f"  {k}: {wd.get(k, 0)}")
