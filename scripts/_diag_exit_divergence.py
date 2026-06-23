"""
DIAG: proc live zavira BOS driv nez backtest dojede na TP_WAVE_N.
Porovna per-bar trend stav engine (all_waves z pine sim) vs live (detect_waves),
plus _close_bos_flip_bar_indices, kolem konkretni vlny.

Spusteni: .venv\\Scripts\\python.exe scripts/_diag_exit_divergence.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DATE_FROM, DATE_TO = "2025-11-10", "2026-05-09"
TARGETS = ["202601201530", "202601282100", "202603031300"]


def main() -> None:
    from backtest.data_loader import filter_by_date_range, load_csv
    from backtest.engine import BacktestEngine
    from backtest.stats import classify_position_kind
    from config.bot_config import LIVE_BOT_CONFIG
    from config.position_modes import resolve_grid_engine_config
    from strategy.wave_detection import detect_waves
    from strategy.trend_bos import compute_trend_states_per_bar

    df = filter_by_date_range(load_csv("data/EURUSD_M30.csv"), DATE_FROM, DATE_TO).reset_index(drop=True)
    cfg = resolve_grid_engine_config(LIVE_BOT_CONFIG)

    eng = BacktestEngine(cfg)
    closed = eng.run(df, retain_wave_snapshot=True)

    def is_wave(t):
        return classify_position_kind(
            is_pp=bool(getattr(t, "is_pp", 0)), is_counter=bool(getattr(t, "is_counter", 0)),
            is_bos_reentry=bool(getattr(t, "is_bos_reentry", 0)),
            is_two_sided_mirror=bool(getattr(t, "is_two_sided_mirror", 0)),
            is_ext=bool(getattr(t, "is_ext", 0)),
            entry_tag=str(getattr(t, "entry_tag", "base"))) == "WAVE"

    trades = {str(t.wave_time): t for t in closed if is_wave(t)}

    # engine trend states (all_waves z pine sim) + close-flip bary
    eng_ts = eng.trend_states_per_bar
    eng_flip = set(getattr(eng, "_close_bos_flip_bar_indices", set()))

    # live trend states (detect_waves)
    live_waves = detect_waves(df, cfg)
    live_ts = compute_trend_states_per_bar(df, live_waves, cfg)

    # globalni diff per-bar smeru
    n = min(len(eng_ts), len(live_ts))
    global_diff = [i for i in range(n)
                   if eng_ts[i].direction != live_ts[i].direction]
    print(f"baru: eng_ts={len(eng_ts)} live_ts={len(live_ts)} "
          f"GLOBAL per-bar direction diff: {len(global_diff)}/{n}")
    if global_diff:
        print("  prvni diffy:", global_diff[:20])

    from strategy.wave_sequence import is_bos_flip_follower_trade
    from infra.orders import _Mt5PositionTradeView

    for wt in TARGETS:
        t = trades.get(wt)
        print("\n" + "=" * 64)
        if t is None:
            print(f"{wt}: NENI mezi WAVE obchody")
            continue
        eb = int(t.close_bar) - int(t.bars_held)
        cb = int(t.close_bar)
        print(f"{wt}: dir={'BUY' if t.dir==1 else 'SELL'} entry_bar={eb} "
              f"close_bar={cb} reason={t.close_reason} pnl={t.pnl_usd:.0f}")
        # engine trade flagy
        print(f"  ENGINE flags: is_ext={getattr(t,'is_ext',0)} "
              f"is_counter={getattr(t,'is_counter',0)} "
              f"is_two_sided={getattr(t,'is_two_sided_mirror',0)} "
              f"is_bos_reentry={getattr(t,'is_bos_reentry',0)} "
              f"entry_tag={getattr(t,'entry_tag','?')}")
        print(f"  ENGINE is_bos_flip_follower = {is_bos_flip_follower_trade(t)}")
        # live by pozici dal comment podle typu — simuluj 'W<wt>' (plain WAVE)
        # vs 'EWP_<wt>' (ext primary). Ukaz, jak by ji live rozpoznal.
        for label, comment in (("plain W", f"W{wt}"), ("EWP_", f"EWP_{wt}")):
            tv = _Mt5PositionTradeView(pos_dir=int(t.dir), comment=comment)
            print(f"  LIVE view [{label}]: is_ext={tv.is_ext} is_counter={tv.is_counter} "
                  f"is_two_sided={tv.is_two_sided_mirror} entry_tag={tv.entry_tag} "
                  f"-> flip_follower={is_bos_flip_follower_trade(tv)}")
        lo, hi = max(0, eb - 2), min(n, cb + 6)
        print(f"  bar   eng_dir   live_dir  engFlip  liveFlip  DIFF")
        for i in range(lo, hi):
            ed = eng_ts[i].direction
            ld = live_ts[i].direction
            ef = "F" if i in eng_flip else "."
            # live flip = zmena smeru oproti i-1
            lf = "F" if (i > 0 and live_ts[i].direction != live_ts[i - 1].direction
                         and live_ts[i - 1].direction != "neutral") else "."
            mark = "  <<<" if ed != ld else ""
            tag = "  [ENTRY]" if i == eb else ("  [CLOSE]" if i == cb else "")
            print(f"  {i:>5} {ed:>9} {ld:>9}    {ef:>4}     {lf:>4}   {mark}{tag}")


if __name__ == "__main__":
    main()
