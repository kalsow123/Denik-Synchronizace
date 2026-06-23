"""
MEASURE: kolik z divergence trend-stavu (engine all_waves/pine_sim vs live
detect_waves) je rozdil DETEKCE VLN (bug) vs legitimni kauzalita, a kolik
PnL na spolecnych vlnach na tom visi.

Vystup:
  1) per-bar trend diff engine vs live (138 baru) — souvisla okna.
  2) porovnani wave-setu: pine_sim vs detect_waves (wave_time mnoziny + dir).
  3) ktere COMMON wave obchody (engine) maji svuj zivot [entry..close]
     uvnitr divergencniho okna  -> "trend-source exposed".

Spusteni: .venv\\Scripts\\python.exe scripts/_diag_trend_source_split.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DATE_FROM, DATE_TO = "2025-11-10", "2026-05-09"


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

    eng_ts = eng.trend_states_per_bar
    eng_waves = eng._all_waves
    live_waves = detect_waves(df, cfg)
    live_ts = compute_trend_states_per_bar(df, live_waves, cfg)

    n = min(len(eng_ts), len(live_ts))
    diff_bars = [i for i in range(n) if eng_ts[i].direction != live_ts[i].direction]

    # souvisla okna
    windows = []
    if diff_bars:
        s = p = diff_bars[0]
        for b in diff_bars[1:]:
            if b == p + 1:
                p = b
            else:
                windows.append((s, p))
                s = p = b
        windows.append((s, p))

    print(f"DIVERGENCE trend per bar: {len(diff_bars)}/{n} baru, {len(windows)} oken")
    for (s, e) in windows:
        print(f"  okno [{s}..{e}]  delka {e-s+1}  eng={eng_ts[s].direction}/live={live_ts[s].direction}")

    # wave-set porovnani
    eng_wt = {str(w["wave_time"]) for w in eng_waves}
    live_wt = {str(w["wave_time"]) for w in live_waves}
    only_eng = eng_wt - live_wt
    only_live = live_wt - eng_wt
    print(f"\nWAVE-SET: pine_sim={len(eng_wt)}  detect_waves={len(live_wt)}  "
          f"spolecnych={len(eng_wt & live_wt)}")
    print(f"  jen pine_sim (engine vidi, live ne): {len(only_eng)}")
    print(f"  jen detect_waves (live vidi, engine ne): {len(only_live)}")
    if only_eng:
        print("    pine_sim-only:", sorted(only_eng)[:30])
    if only_live:
        print("    detect-only :", sorted(only_live)[:30])

    # COMMON wave obchody (engine) — expozice na divergencni okna
    def is_wave(t):
        return classify_position_kind(
            is_pp=bool(getattr(t, "is_pp", 0)), is_counter=bool(getattr(t, "is_counter", 0)),
            is_bos_reentry=bool(getattr(t, "is_bos_reentry", 0)),
            is_two_sided_mirror=bool(getattr(t, "is_two_sided_mirror", 0)),
            is_ext=bool(getattr(t, "is_ext", 0)),
            entry_tag=str(getattr(t, "entry_tag", "base"))) == "WAVE"

    diff_set = set(diff_bars)
    wave_trades = [t for t in closed if is_wave(t)]
    exposed = []
    not_exposed = []
    for t in wave_trades:
        eb = int(t.close_bar) - int(t.bars_held)
        cb = int(t.close_bar)
        touches = any((b in diff_set) for b in range(eb, cb + 1))
        (exposed if touches else not_exposed).append(t)

    exp_pnl = sum(t.pnl_usd for t in exposed)
    nexp_pnl = sum(t.pnl_usd for t in not_exposed)
    print(f"\nENGINE WAVE obchody: {len(wave_trades)}")
    print(f"  TREND-SOURCE EXPOSED (zivot protina divergencni bar): {len(exposed)}  "
          f"engine_pnl={exp_pnl:.0f}")
    print(f"  NEexposed (trend identicky cely zivot):               {len(not_exposed)}  "
          f"engine_pnl={nexp_pnl:.0f}")


if __name__ == "__main__":
    main()
