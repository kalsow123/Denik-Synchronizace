"""
Pro vsech 32 BT-only WAVE vln zjisti SKUTECNY engine entry mechanismus
(bypass / two_sided mirror / normal) + per-wave a per-bar trend v okamziku vstupu.

Spusteni: .venv\\Scripts\\python.exe scripts/_diag_btonly_mechanism.py
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DATE_FROM, DATE_TO = "2025-11-10", "2026-05-09"


def main() -> None:
    import backtest.engine as em
    from backtest.data_loader import filter_by_date_range, load_csv
    from backtest.engine import BacktestEngine
    from backtest.stats import classify_position_kind
    from config.bot_config import LIVE_BOT_CONFIG
    from config.position_modes import resolve_grid_engine_config
    from strategy.trend_bos import wave_allowed_for_entry

    # BT-only set z produkcniho E2E behu (sekce 18C / posledni diag)
    BT_ONLY = set("""202511261030 202511261830 202512051800 202512102200 202601021800
202601122230 202601220130 202601261800 202602130930 202602131530 202602171730
202602180100 202602192330 202602230130 202602231400 202602250900 202602251930
202603041300 202603051030 202603160400 202603170900 202603171200 202603181600
202603242300 202603250100 202603310430 202604082200 202604092030 202604171600
202604301400 202605061930 202605072330""".split())

    df = filter_by_date_range(load_csv("data/EURUSD_M30.csv"), DATE_FROM, DATE_TO).reset_index(drop=True)
    cfg = resolve_grid_engine_config(LIVE_BOT_CONFIG)

    calls = defaultdict(list)
    orig = em.BacktestEngine._process_new_wave

    def spy(self, wave, bar_idx, bar_time, bar, *, bypass_trend_filter=False, is_two_sided_mirror=False):
        wt = str(wave.get("wave_time", "") or "")
        if wt in BT_ONLY:
            ts = self.trend_states_per_wave.get(wave["wave_time"])
            allowed, reason = wave_allowed_for_entry(wave, ts, cfg)
            calls[wt].append((int(bar_idx), bool(bypass_trend_filter), bool(is_two_sided_mirror),
                              getattr(ts, "direction", None), allowed, reason,
                              wt in self._bos_wave_times))
        return orig(self, wave, bar_idx, bar_time, bar,
                    bypass_trend_filter=bypass_trend_filter, is_two_sided_mirror=is_two_sided_mirror)

    em.BacktestEngine._process_new_wave = spy
    closed = BacktestEngine(cfg).run(df, retain_wave_snapshot=False)
    em.BacktestEngine._process_new_wave = orig

    pnl = defaultdict(float)
    for t in closed:
        if str(t.wave_time) in BT_ONLY:
            pnl[str(t.wave_time)] += t.pnl_usd

    rows = []
    for wt in sorted(BT_ONLY):
        cs = calls.get(wt, [])
        if not cs:
            rows.append((pnl.get(wt, 0.0), wt, "NO_PNW_CALL", ""))
            continue
        # vyber posledni vstupni volani (to ktere realne otevrelo)
        c = cs[-1]
        bar, byp, mir, tsd, al, rs, inbos = c
        if mir:
            mech = "TWO_SIDED_MIRROR"
        elif byp:
            mech = "BYPASS(retro/bos)"
        elif al:
            mech = "NORMAL_allowed"
        else:
            mech = "NORMAL_blocked?"
        rows.append((pnl.get(wt, 0.0), wt, mech,
                     f"bar={bar} ts={tsd} allowed={al}({rs}) inbos={inbos} ncalls={len(cs)}"))

    cat_pnl = defaultdict(float)
    cat_cnt = defaultdict(int)
    for p, wt, mech, _ in rows:
        cat_pnl[mech] += p
        cat_cnt[mech] += 1

    print("=" * 78)
    print("BT-only ENGINE ENTRY MECHANISMUS:")
    for mech in sorted(cat_pnl, key=lambda m: -cat_pnl[m]):
        print(f"  {mech:<20} vln={cat_cnt[mech]:>2}  pnl={cat_pnl[mech]:>9.0f}")
    print("\nDetaily (pnl vzestupne):")
    rows.sort()
    for p, wt, mech, info in rows:
        print(f"  {wt} pnl={p:>7.0f}  {mech:<18} {info}")


if __name__ == "__main__":
    main()
