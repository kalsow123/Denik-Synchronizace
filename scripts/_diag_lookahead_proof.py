"""DUKAZ look-ahead: pro vlny, kde engine vstupuje na flip_bar < birth, porovnej
draw_right (bar, na kterem je cenovy box vlny KOMPLETNI = EP/SL znamy) s flip_bar.

  draw_right > flip_bar  => engine na flip_baru jeste NEZNA cenu vlny  => PRAVY look-ahead
  draw_right <= flip_bar => box hotovy, jen Pine 'birth' label laguje (mene zavazne)
"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DATE_FROM = "2025-11-10"
DATE_TO = "2026-05-09"
CSV = ROOT / "data" / "EURUSD_M30.csv"

# 11 vln s engine entry PRED birth (z _diag_flip_vs_birth.py)
LOOKAHEAD = [
    '202511261830', '202512102200', '202601021800', '202601261800',
    '202602131530', '202602230130', '202603170900', '202603171200',
    '202603181600', '202603242300', '202604171600',
]


def main() -> None:
    from backtest.data_loader import filter_by_date_range, load_csv
    from config.bot_config import LIVE_BOT_CONFIG
    from config.position_modes import resolve_grid_engine_config
    from strategy.wave_detection_pine import run_pine_wave_simulation
    from strategy.trend_bos import (
        compute_bos_wave_flip_map, reconcile_bos_flip_map_with_wave_sequence,
        _detect_close_bos_timeline_flips,
    )
    from strategy.wave_sequence import sync_wave_sequence_state

    cfg = resolve_grid_engine_config(LIVE_BOT_CONFIG)
    df = filter_by_date_range(load_csv(str(CSV)), DATE_FROM, DATE_TO).reset_index(drop=True)
    waves, birth, _, _ = run_pine_wave_simulation(df, cfg)
    by_wt = {str(w["wave_time"]): w for w in waves}
    seq_info, _ = sync_wave_sequence_state(df, waves, cfg)

    flips = _detect_close_bos_timeline_flips(df, waves, cfg, wave_birth_bars=birth)
    flip_map = reconcile_bos_flip_map_with_wave_sequence(
        compute_bos_wave_flip_map(df, waves, cfg, wave_birth_bars=birth),
        flips, waves, seq_info, birth,
    )
    flip_bar_by_wt = {str(wt): int(b) for b, wt in flip_map.items()}

    print(f"{'wave_time':<14}{'draw_left':>10}{'draw_right':>11}{'flip_bar':>9}{'birth':>7}  verdikt")
    true_la = 0
    for wt in LOOKAHEAD:
        w = by_wt.get(wt)
        if w is None:
            print(f"{wt:<14}  (neni ve waves)")
            continue
        dl = int(w.get("draw_left", -1))
        dr = int(w.get("draw_right", -1))
        fb = flip_bar_by_wt.get(wt)
        b = birth.get(wt)
        if fb is not None and dr > fb:
            verd = f"PRAVY LOOK-AHEAD (draw_right {dr} > flip {fb})"
            true_la += 1
        elif fb is not None and dr <= fb:
            verd = f"box hotovy do flip (draw_right {dr} <= flip {fb}); jen birth label laguje"
        else:
            verd = "flip_bar=None (neni v bos_flip_map - jina cesta)"
        print(f"{wt:<14}{dl:>10}{dr:>11}{str(fb):>9}{str(b):>7}  {verd}")
    print(f"\n  PRAVY look-ahead (draw_right za flip barem): {true_la}/{len(LOOKAHEAD)}")


if __name__ == "__main__":
    main()
